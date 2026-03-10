"""LinkedIn API client — post text + image/video using Community Management API."""

import os
import time
import requests

from auth import load_token

API_VERSION = "202503"


def _headers(access_token):
    return {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": API_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def _person_urn(token_data):
    return f"urn:li:person:{token_data['person_id']}"


def upload_image(access_token, person_urn, image_path):
    """Upload image to LinkedIn. Returns the image URN."""
    # Step 1: Initialize upload
    resp = requests.post(
        "https://api.linkedin.com/rest/images?action=initializeUpload",
        headers=_headers(access_token),
        json={
            "initializeUploadRequest": {
                "owner": person_urn,
            }
        },
    )
    resp.raise_for_status()
    upload_data = resp.json()["value"]
    upload_url = upload_data["uploadUrl"]
    image_urn = upload_data["image"]

    # Step 2: Upload binary
    with open(image_path, "rb") as f:
        image_bytes = f.read()

    resp = requests.put(
        upload_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/octet-stream",
        },
        data=image_bytes,
    )
    resp.raise_for_status()

    return image_urn


def upload_video(access_token, person_urn, video_path):
    """Upload video to LinkedIn. Returns the video URN."""
    file_size = os.path.getsize(video_path)

    # Step 1: Initialize upload
    resp = requests.post(
        "https://api.linkedin.com/rest/videos?action=initializeUpload",
        headers=_headers(access_token),
        json={
            "initializeUploadRequest": {
                "owner": person_urn,
                "fileSizeBytes": file_size,
            }
        },
    )
    resp.raise_for_status()
    upload_data = resp.json()["value"]
    upload_url = upload_data["uploadInstructions"][0]["uploadUrl"]
    video_urn = upload_data["video"]

    # Step 2: Upload binary
    with open(video_path, "rb") as f:
        video_bytes = f.read()

    resp = requests.put(
        upload_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/octet-stream",
        },
        data=video_bytes,
    )
    resp.raise_for_status()

    # Step 3: Wait for processing (poll status)
    for _ in range(30):
        time.sleep(2)
        resp = requests.get(
            f"https://api.linkedin.com/rest/videos/{video_urn}",
            headers=_headers(access_token),
        )
        if resp.ok:
            status = resp.json().get("status")
            if status == "AVAILABLE":
                return video_urn
            if status in ("PROCESSING_FAILED", "FAILED"):
                raise RuntimeError(f"Video processing failed: {status}")

    raise RuntimeError("Video processing timed out")


def create_post_with_video(content, video_path):
    """Create a LinkedIn post with a video. Returns the post URN."""
    token_data = load_token()
    if not token_data:
        raise RuntimeError("No valid LinkedIn token. Run: python auth.py")

    access_token = token_data["access_token"]
    person_urn = _person_urn(token_data)

    video_urn = upload_video(access_token, person_urn, video_path)

    post_body = {
        "author": person_urn,
        "commentary": content,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "content": {
            "media": {
                "title": "AI & Tech Trends",
                "id": video_urn,
            }
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    resp = requests.post(
        "https://api.linkedin.com/rest/posts",
        headers=_headers(access_token),
        json=post_body,
    )
    resp.raise_for_status()
    return resp.headers.get("x-restli-id", "unknown")


def create_post_with_image(content, image_path):
    """Create a LinkedIn post with an image. Returns the post URN."""
    token_data = load_token()
    if not token_data:
        raise RuntimeError("No valid LinkedIn token. Run: python auth.py")

    access_token = token_data["access_token"]
    person_urn = _person_urn(token_data)

    # Upload image
    image_urn = upload_image(access_token, person_urn, image_path)

    # Create post
    post_body = {
        "author": person_urn,
        "commentary": content,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "content": {
            "media": {
                "title": "AI & Tech Trends",
                "id": image_urn,
            }
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    resp = requests.post(
        "https://api.linkedin.com/rest/posts",
        headers=_headers(access_token),
        json=post_body,
    )
    resp.raise_for_status()

    post_urn = resp.headers.get("x-restli-id", "unknown")
    return post_urn


def get_post_metrics(post_urn):
    """Get engagement metrics for a LinkedIn post."""
    from urllib.parse import quote

    token_data = load_token()
    if not token_data:
        return None

    access_token = token_data["access_token"]
    encoded_urn = quote(post_urn, safe="")

    # Try socialActions endpoint
    try:
        resp = requests.get(
            f"https://api.linkedin.com/rest/socialActions/{encoded_urn}",
            headers=_headers(access_token),
        )
        if resp.ok:
            data = resp.json()
            return {
                "likes": data.get("likesSummary", {}).get("totalLikes", 0),
                "comments": data.get("commentsSummary", {}).get("totalFirstLevelComments", 0),
                "shares": data.get("sharesSummary", {}).get("totalShares", 0),
            }
    except Exception:
        pass

    # Try posts endpoint as fallback
    try:
        resp = requests.get(
            f"https://api.linkedin.com/rest/posts/{encoded_urn}",
            headers=_headers(access_token),
        )
        if resp.ok:
            data = resp.json()
            return {
                "likes": data.get("numLikes", 0),
                "comments": data.get("numComments", 0),
                "shares": data.get("numShares", 0),
            }
    except Exception:
        pass

    return {"likes": 0, "comments": 0, "shares": 0}


def create_text_post(content):
    """Create a text-only LinkedIn post."""
    token_data = load_token()
    if not token_data:
        raise RuntimeError("No valid LinkedIn token. Run: python auth.py")

    access_token = token_data["access_token"]
    person_urn = _person_urn(token_data)

    post_body = {
        "author": person_urn,
        "commentary": content,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    resp = requests.post(
        "https://api.linkedin.com/rest/posts",
        headers=_headers(access_token),
        json=post_body,
    )
    resp.raise_for_status()
    return resp.headers.get("x-restli-id", "unknown")
