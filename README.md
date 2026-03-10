# LinkedIn Autoposter & AI Assistant

A powerful, AI-driven tool to automate and enhance your LinkedIn presence. This project combines an advanced AI agent, real-time trend monitoring, and endless customization options—all controlled via a convenient Telegram bot interface.

## 🚀 Features

- **AI-Powered Agent**: A versatile assistant (powered by OpenAI) that can answer questions, research topics, and draft content specifically for LinkedIn.
- **Trend Monitoring**: Automatically fetches the latest tech and AI news from RSS feeds (TechCrunch, The Verge, etc.) and influential X (Twitter) accounts.
- **Content Generation**: 
  - Generates engaging LinkedIn posts based on trends or your specific prompts.
  - Creates custom AI images (DALL-E) to accompany your posts.
  - Supports video uploads.
- **Draft Management**: Save, edit, and refine drafts before they go live.
- **Scheduling**: Schedule posts for optimal times directly from the chat.
- **Memory System**: The agent "remembers" your preferences, writing style, and past conversations to strictly tailor content to you.
- **Telegram Interface**: Full control of the entire pipeline through a user-friendly Telegram bot.

## 🛠️ Prerequisites

- Python 3.10+
- A LinkedIn account (and an App created in the [LinkedIn Developer Portal](https://www.linkedin.com/developers/))
- An OpenAI API Key
- A Telegram Bot Token (from [BotFather](https://t.me/BotFather))

## 📦 Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/linkedin-autoposter.git
    cd linkedin-autoposter
    ```

2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## ⚙️ Configuration

1.  **Create a `.env` file** in the root directory and add your API keys:

    ```ini
    # OpenAI
    OPENAI_API_KEY=your_openai_api_key

    # LinkedIn App Credentials
    LINKEDIN_CLIENT_ID=your_linkedin_client_id
    LINKEDIN_CLIENT_SECRET=your_linkedin_client_secret

    # Telegram Bot
    TELEGRAM_BOT_TOKEN=your_telegram_bot_token
    ```

2.  **Authenticate with LinkedIn:**
    Run the authentication script to generate your OAuth token (`token.json`).
    ```bash
    python auth.py
    ```
    - Follow the instructions in the terminal.
    - Open the link provided, authorize the app, and you're set.

## 🚀 Usage

1.  **Start the Telegram Bot:**
    ```bash
    python bot.py
    ```

2.  **Interact via Telegram:**
    - Open your bot in Telegram.
    - **Chat**: Talk to the agent naturally. Ask questions, discuss tech news, or brainstorm ideas.
    - **Create a Post**: Ask the agent to "create a post about [topic]" or "check for trends".
    - **Refine**: The agent will generate a draft and an image. You can ask for changes (e.g., "make the tone more professional", "change the image").
    - **Approve**: Once satisfied, approve the draft to post it immediately or schedule it.

## 📂 Project Structure

- `agent.py`: Core logic for the AI agent, memory management, and decision making.
- `bot.py`: Telegram bot implementation, handling user status and commands.
- `pipeline.py`: Content pipeline – fetches RSS feeds, scrapes URLs, and generates images.
- `linkedin.py`: LinkedIn API client for uploading images/videos and publishing posts.
- `auth.py`: OAuth 2.0 flow helper to get your LinkedIn access token.
- `db.py`: SQLite database operations for storing drafts, history, and user memory.
- `requirements.txt`: List of Python dependencies.

## 🛡️ License

[MIT License](LICENSE)
