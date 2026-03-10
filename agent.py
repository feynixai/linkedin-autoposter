"""
LinkedIn Autoposter Agent — GPT-5.4 with tools.
The agent decides what to do: search web, fetch URLs, generate content, images, etc.
"""

import json
import os
import re
from datetime import datetime

import openai
from dotenv import load_dotenv

from db import (
    save_draft, get_latest_draft, get_draft, get_recent_topics,
    update_draft_content, mark_posted, mark_skipped, set_scheduled_time,
    get_scheduled_posts, remember, forget, get_all_memories, get_top_posts,
)
from pipeline import fetch_url_content, fetch_trends, generate_image, download_image_from_url, download_video_from_url, IMAGES_DIR
from linkedin import create_post_with_image, create_post_with_video

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

SYSTEM_PROMPT = """You are a versatile AI assistant and LinkedIn autoposter agent for a tech professional named Arun. You can have normal conversations, answer questions, AND help create/manage LinkedIn posts.

IMPORTANT: You are a general-purpose agent. NOT everything is about creating posts.
- If the user asks a question, just answer it. Do NOT create a post unless asked.
- If the user shares info/links WITHOUT asking for a post, just acknowledge and discuss.
- ONLY create/save a draft when the user explicitly wants a post (e.g., "make a post", "write a post about this", "create a LinkedIn post", "generate a post").

WHEN EXPLAINING OR ANSWERING QUESTIONS (not posts):
- Be clear and concise — no fluff or filler
- Use short lines, bullet points, or numbered steps
- Break down complex topics simply — like explaining to a smart friend
- Use analogies and real-world examples to make concepts click
- Use "→" arrows to show cause/effect
- Bold the key takeaway
- If comparing things, use a quick side-by-side format
- Keep it conversational, not textbook-style

You have tools available to:
- Search the web for trending topics, news, and context
- Fetch content from specific URLs (tweets, articles, blogs)
- Fetch RSS tech news feeds
- Generate AI images for posts
- Save/edit/manage drafts
- Store user preferences in memory
- Post to LinkedIn

WORKFLOW for creating posts (ONLY when asked):
1. First, gather context — search the web or fetch URLs the user shared
2. Check stored memories for user preferences (tone, style rules, etc.)
3. Write a compelling LinkedIn post based on real, factual content
4. Generate a relevant, eye-catching image
5. Save as draft and show preview

RULES:
- ALWAYS base posts on REAL content you've fetched — never hallucinate facts
- When given URLs, ALWAYS fetch them first to get actual content
- When asked to search, use web_search to find real current information
- Include source links/citations in posts when referencing specific content
- IMAGE GENERATION IS CRITICAL. Always generate EXPLANATORY, INFORMATIVE images:
  * Prefer: flowcharts, block diagrams, architecture diagrams, comparison charts, process flows, step-by-step visuals, infographics with key stats/numbers
  * Include TEXT LABELS in the image — key terms, numbers, names, arrows showing relationships
  * Think: "If someone only saw the image, could they understand the core concept?"
  * Example: For "AI agents replacing SaaS" → a diagram showing Traditional SaaS stack on left vs AI Agent stack on right with arrows
  * Example: For "GPT-5 benchmarks" → a chart/comparison visual showing performance metrics
  * Style: clean, modern, dark or gradient background, bold typography, tech aesthetic
  * NEVER generate generic stock-photo-style images (handshakes, abstract waves, random robots)
- Follow all stored user preferences from memory
- POST WRITING STYLE — make it ENGAGING, not boring paragraphs:
  * Start with a BOLD hook — a surprising stat, hot take, or provocative question
  * Use SHORT lines (1-2 sentences max per line) with line breaks between them
  * Break complex ideas into numbered lists or bullet points
  * Use "→" arrows to show cause/effect or progression
  * Add a personal take or prediction — don't just summarize news
  * End with a question to drive comments
  * 150-250 words, 3-5 hashtags at the end
  * Tone: confident, conversational, like texting a smart friend — NOT corporate/formal
  * Example structure:
    [Hook — 1 bold line]

    [Context — 2-3 short lines explaining what happened]

    [Breakdown — numbered list or bullets with key points]

    [Your take — 1-2 lines with opinion/prediction]

    [CTA question]

    #hashtags
- When user asks to change/regenerate the image, ONLY change the image, keep the text
- When user shares information and asks to make a post, use ALL the information they shared
- Honor every command the user gives — edits, regeneration, skip, post, etc.
- When a tweet has VIDEOS (has_video=true), use download_web_video to download the video and use it as the post media. LinkedIn supports video posts. Only fall back to images/thumbnails if video download fails.
- When a tweet has IMAGES (photos), use download_web_image to use the original image from the tweet.

Current time: {current_time} IST
"""

# Tool definitions for the agent
TOOLS = [
    {
        "type": "web_search_preview",
        "search_context_size": "medium",
    },
    {
        "type": "function",
        "name": "fetch_url",
        "description": "Fetch content from a URL (tweet, article, blog post). Returns the actual text content, author, and any images. Use this for any URL the user shares.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"}
            },
            "required": ["url"]
        }
    },
    {
        "type": "function",
        "name": "fetch_rss_trends",
        "description": "Fetch latest tech/AI headlines from RSS feeds (Hacker News, TechCrunch, The Verge, Ars Technica). Use this to find trending topics when the user wants a general post about current tech news.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "type": "function",
        "name": "generate_post_image",
        "description": "Generate an EXPLANATORY AI image for a LinkedIn post. The image MUST be informative — think diagrams, flowcharts, infographics, comparison charts, or architecture visuals with text labels. NOT generic stock photos.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed image prompt for an INFORMATIVE visual. MUST include: (1) Type of visual: flowchart/block diagram/comparison chart/infographic/architecture diagram (2) Specific text labels, key terms, numbers to show (3) Layout and flow direction (4) Style: clean modern tech aesthetic, dark gradient background, bold typography. Example: 'A clean block diagram on dark gradient background showing the evolution of AI: Left block labeled \"Rule-Based AI\" with gear icons → middle block \"Machine Learning\" with neural network icon → right block \"AI Agents\" with autonomous robot icon. Arrows connecting each stage. Below each block, key characteristics in smaller text. Modern tech aesthetic, blue and purple accent colors, bold sans-serif typography'"
                }
            },
            "required": ["prompt"]
        }
    },
    {
        "type": "function",
        "name": "download_web_image",
        "description": "Download an image from a URL to use as the post image instead of generating one. Use when a web/tweet image is more appropriate.",
        "parameters": {
            "type": "object",
            "properties": {
                "image_url": {"type": "string", "description": "URL of the image to download"}
            },
            "required": ["image_url"]
        }
    },
    {
        "type": "function",
        "name": "download_web_video",
        "description": "Download a video from a URL (e.g. from a tweet) to use as the post media. LinkedIn supports video posts. Use when the tweet/source has a video.",
        "parameters": {
            "type": "object",
            "properties": {
                "video_url": {"type": "string", "description": "URL of the video to download"}
            },
            "required": ["video_url"]
        }
    },
    {
        "type": "function",
        "name": "save_post_draft",
        "description": "Save a LinkedIn post as a draft. Returns the draft ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The full LinkedIn post text"},
                "image_path": {"type": "string", "description": "Path to the image file"},
                "topics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of topics covered in the post"
                }
            },
            "required": ["content", "image_path", "topics"]
        }
    },
    {
        "type": "function",
        "name": "update_draft_text",
        "description": "Update the text of an existing draft. Use when the user wants to edit the post text.",
        "parameters": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "integer", "description": "The draft ID to update"},
                "new_content": {"type": "string", "description": "The new post text"}
            },
            "required": ["draft_id", "new_content"]
        }
    },
    {
        "type": "function",
        "name": "update_draft_image",
        "description": "Update ONLY the image of an existing draft. Use when user says 'change image', 'new image', 'regenerate image', etc.",
        "parameters": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "integer", "description": "The draft ID to update"},
                "image_path": {"type": "string", "description": "Path to the new image file"}
            },
            "required": ["draft_id", "image_path"]
        }
    },
    {
        "type": "function",
        "name": "get_current_draft",
        "description": "Get the current/latest draft post. Returns the draft text, image path, and ID.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "type": "function",
        "name": "get_recent_posted_topics",
        "description": "Get topics from posts made in the last 7 days, to avoid repeating them.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "type": "function",
        "name": "remember_preference",
        "description": "Store a user preference that will be applied to ALL future posts. Examples: 'never use dashes', 'always casual tone', 'include my name Arun in posts'.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Short key for the preference"},
                "value": {"type": "string", "description": "The preference description"}
            },
            "required": ["key", "value"]
        }
    },
    {
        "type": "function",
        "name": "forget_preference",
        "description": "Remove a stored user preference.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "The preference key to remove"}
            },
            "required": ["key"]
        }
    },
    {
        "type": "function",
        "name": "get_memories",
        "description": "Get all stored user preferences/memories.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "type": "function",
        "name": "post_to_linkedin",
        "description": "Publish a draft to LinkedIn immediately. Only call this when the user explicitly says to post.",
        "parameters": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "integer", "description": "The draft ID to publish"}
            },
            "required": ["draft_id"]
        }
    },
    {
        "type": "function",
        "name": "schedule_linkedin_post",
        "description": "Schedule a draft to be published on LinkedIn at a specific time. Uses LinkedIn's native scheduling. The post will appear as scheduled in LinkedIn's UI. Use when user says 'schedule for 3pm', 'post tomorrow at 10am', etc.",
        "parameters": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "integer", "description": "The draft ID to schedule"},
                "scheduled_time": {"type": "string", "description": "ISO format datetime string in IST, e.g. '2026-03-08T15:00:00'. Parse the user's natural language time into this format."}
            },
            "required": ["draft_id", "scheduled_time"]
        }
    },
    {
        "type": "function",
        "name": "save_post_metrics",
        "description": "Save engagement metrics for a posted LinkedIn post. Use when the user reports how a post performed (e.g. 'post 12 got 50 likes 10 comments 3 shares').",
        "parameters": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "integer", "description": "The post/draft ID"},
                "likes": {"type": "integer", "description": "Number of likes"},
                "comments": {"type": "integer", "description": "Number of comments"},
                "shares": {"type": "integer", "description": "Number of shares/reposts"}
            },
            "required": ["draft_id", "likes", "comments", "shares"]
        }
    },
    {
        "type": "function",
        "name": "skip_draft",
        "description": "Skip/discard the current draft.",
        "parameters": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "integer", "description": "The draft ID to skip"}
            },
            "required": ["draft_id"]
        }
    },
]


def execute_tool(name, args):
    """Execute a tool call and return the result."""
    if name == "fetch_url":
        result = fetch_url_content(args["url"])
        return json.dumps(result)

    elif name == "fetch_rss_trends":
        trends = fetch_trends()
        return json.dumps(trends[:20])

    elif name == "generate_post_image":
        image_path = generate_image(args["prompt"])
        return json.dumps({"image_path": image_path, "status": "generated"})

    elif name == "download_web_image":
        image_path = download_image_from_url(args["image_url"])
        if image_path:
            return json.dumps({"image_path": image_path, "status": "downloaded"})
        return json.dumps({"error": "Failed to download image"})

    elif name == "download_web_video":
        video_path = download_video_from_url(args["video_url"])
        if video_path:
            return json.dumps({"video_path": video_path, "status": "downloaded"})
        return json.dumps({"error": "Failed to download video"})

    elif name == "save_post_draft":
        draft_id = save_draft(
            args["content"],
            args.get("image_prompt", ""),
            args["image_path"],
            args.get("topics", []),
        )
        return json.dumps({"draft_id": draft_id, "status": "saved"})

    elif name == "update_draft_text":
        update_draft_content(args["draft_id"], args["new_content"])
        return json.dumps({"status": "updated", "draft_id": args["draft_id"]})

    elif name == "update_draft_image":
        from db import get_conn
        conn = get_conn()
        conn.execute(
            "UPDATE posts SET image_path = ? WHERE id = ?",
            (args["image_path"], args["draft_id"]),
        )
        conn.commit()
        conn.close()
        return json.dumps({"status": "image_updated", "draft_id": args["draft_id"]})

    elif name == "get_current_draft":
        draft = get_latest_draft()
        if draft:
            return json.dumps({"id": draft["id"], "content": draft["content"], "image_path": draft["image_path"], "status": draft["status"]})
        return json.dumps({"error": "No draft found"})

    elif name == "get_recent_posted_topics":
        topics = get_recent_topics(days=7)
        return json.dumps(topics)

    elif name == "remember_preference":
        remember(args["key"], args["value"])
        return json.dumps({"status": "remembered", "key": args["key"], "value": args["value"]})

    elif name == "forget_preference":
        forget(args["key"])
        return json.dumps({"status": "forgotten", "key": args["key"]})

    elif name == "get_memories":
        memories = get_all_memories()
        return json.dumps(memories)

    elif name == "post_to_linkedin":
        draft = get_draft(args["draft_id"])
        if not draft:
            return json.dumps({"error": "Draft not found"})
        media_path = draft.get("image_path", "")
        if media_path and media_path.endswith(".mp4"):
            post_urn = create_post_with_video(draft["content"], media_path)
        else:
            post_urn = create_post_with_image(draft["content"], media_path)
        mark_posted(args["draft_id"], post_urn)
        return json.dumps({"status": "posted", "post_urn": post_urn})

    elif name == "schedule_linkedin_post":
        draft = get_draft(args["draft_id"])
        if not draft:
            return json.dumps({"error": "Draft not found"})
        # Save the scheduled time in DB — the bot's scheduler will pick it up
        set_scheduled_time(args["draft_id"], args["scheduled_time"])
        return json.dumps({"status": "scheduled", "scheduled_at": args["scheduled_time"], "draft_id": args["draft_id"]})

    elif name == "save_post_metrics":
        from db import save_metrics
        save_metrics(args["draft_id"], None, 0, args["likes"], args["comments"], args["shares"])
        return json.dumps({"status": "saved", "draft_id": args["draft_id"]})

    elif name == "skip_draft":
        mark_skipped(args["draft_id"])
        return json.dumps({"status": "skipped"})

    else:
        return json.dumps({"error": f"Unknown tool: {name}"})


def run_agent(user_message, conversation_history=None, think_hard=False):
    """Run the agent loop. Yields (type, data) tuples for streaming updates."""
    client = openai.OpenAI(api_key=OPENAI_API_KEY)

    now = datetime.now()
    system = SYSTEM_PROMPT.format(current_time=now.strftime('%Y-%m-%d %H:%M'))

    # Add memory context to system prompt
    memories = get_all_memories()
    if memories:
        memory_lines = "\n".join(f"- {k}: {v}" for k, v in memories.items())
        system += f"\n\nStored user preferences (ALWAYS follow these):\n{memory_lines}"

    # Add top performing posts for learning
    top_posts = get_top_posts(limit=3)
    if top_posts:
        top_lines = []
        for p in top_posts:
            score = p.get("engagement_score", 0)
            top_lines.append(f"- [{score} engagement] {p['content'][:150]}...")
        system += f"\n\nTOP PERFORMING POSTS (learn from these styles/topics):\n" + "\n".join(top_lines)

    messages = [{"role": "system", "content": system}]
    if conversation_history:
        messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_message})

    # Track if a draft was created or modified during this run
    draft_touched = False
    draft_tools = {"save_post_draft", "update_draft_text", "update_draft_image"}

    max_iterations = 10
    for i in range(max_iterations):
        api_kwargs = dict(
            model="gpt-5.4",
            input=messages,
            tools=TOOLS,
            max_output_tokens=2048,
        )
        if think_hard:
            api_kwargs["reasoning"] = {"effort": "medium"}

        response = client.responses.create(**api_kwargs)

        # Process response
        tool_calls = []
        text_output = ""
        for item in response.output:
            if item.type == "function_call":
                tool_calls.append(item)
            elif item.type == "message":
                for content in item.content:
                    if hasattr(content, "text"):
                        text_output += content.text
            elif item.type == "text":
                text_output += item.text if hasattr(item, "text") else ""

        # If no tool calls, we're done
        if not tool_calls:
            draft = get_latest_draft() if draft_touched else None
            return {"type": "message", "text": text_output, "draft": draft}

        # Execute tool calls — append response output items directly (Responses API format)
        messages.extend(response.output)

        for tc in tool_calls:
            tool_name = tc.name
            tool_args = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments

            yield {"type": "tool_call", "tool": tool_name, "args": tool_args}

            if tool_name in draft_tools:
                draft_touched = True

            result = execute_tool(tool_name, tool_args)
            messages.append({
                "type": "function_call_output",
                "call_id": tc.call_id,
                "output": result,
            })

    draft = get_latest_draft() if draft_touched else None
    return {"type": "message", "text": "Agent reached max iterations.", "draft": draft}


def run_agent_sync(user_message, conversation_history=None):
    """Run agent synchronously, collecting all results."""
    tool_log = []
    final_result = None

    # Detect "think hard" trigger phrases
    lower = user_message.lower()
    think_hard = any(phrase in lower for phrase in ["think hard", "think deeply", "think more", "think carefully"])

    gen = run_agent(user_message, conversation_history=conversation_history, think_hard=think_hard)
    try:
        while True:
            item = next(gen)
            if item["type"] == "tool_call":
                tool_log.append(item)
            else:
                final_result = item
    except StopIteration as e:
        if e.value:
            final_result = e.value

    return {
        "tool_log": tool_log,
        "result": final_result or {"type": "message", "text": "Done", "draft": get_latest_draft()},
    }
