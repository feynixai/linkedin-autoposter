#!/usr/bin/env python3
"""
LinkedIn OAuth 2.0 Authorization Flow.
Run this once to get your access token: python auth.py
"""

import json
import os
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
REDIRECT_URI = "http://localhost:8765/callback"
SCOPES = "openid profile email w_member_social"
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")

auth_code = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        query = parse_qs(urlparse(self.path).query)

        if "code" in query:
            auth_code = query["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Success! You can close this tab.</h1>")
        else:
            error = query.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<h1>Error: {error}</h1>".encode())

    def log_message(self, format, *args):
        pass  # suppress logs


def get_authorization_url():
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
    }
    return f"https://www.linkedin.com/oauth/v2/authorization?{urlencode(params)}"


def exchange_code_for_token(code):
    resp = requests.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    return resp.json()


def get_user_info(access_token):
    resp = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    resp.raise_for_status()
    return resp.json()


def save_token(token_data, user_info):
    token_data["obtained_at"] = int(time.time())
    token_data["person_id"] = user_info.get("sub")
    token_data["name"] = user_info.get("name")
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)
    print(f"Token saved to {TOKEN_FILE}")


def load_token():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE) as f:
        data = json.load(f)
    expires_at = data.get("obtained_at", 0) + data.get("expires_in", 0)
    if time.time() > expires_at - 300:  # 5 min buffer
        print("Token expired. Run `python auth.py` to re-authenticate.")
        return None
    return data


def main():
    global auth_code

    if not CLIENT_ID or not CLIENT_SECRET:
        print("Error: Set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET in .env")
        return

    auth_url = get_authorization_url()
    print(f"\nOpening browser for LinkedIn authorization...\n")
    print(f"If browser doesn't open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    print("Waiting for callback on http://localhost:8765 ...")
    server = HTTPServer(("localhost", 8765), CallbackHandler)
    server.handle_request()  # handle single request

    if not auth_code:
        print("Error: No authorization code received.")
        return

    print("Exchanging code for access token...")
    token_data = exchange_code_for_token(auth_code)

    print("Fetching user info...")
    user_info = get_user_info(token_data["access_token"])
    print(f"Authenticated as: {user_info.get('name')} (sub: {user_info.get('sub')})")

    save_token(token_data, user_info)
    print("\nDone! You can now run: python post.py")


if __name__ == "__main__":
    main()
