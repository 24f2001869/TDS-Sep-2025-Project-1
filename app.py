import os
import threading
import base64
import subprocess
import time
import requests
import shutil
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from pathlib import Path
import google.generativeai as genai

# --- CONFIGURATION ---
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

app = Flask(__name__)
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# --- HELPER FUNCTIONS ---

def get_env_variable(var_name):
    value = os.getenv(var_name)
    if not value:
        raise ValueError(f"FATAL ERROR: Environment variable '{var_name}' is not set.")
    return value


def generate_llm_code(brief, existing_code=None):
    """Use Gemini to generate or update HTML code."""
    print("🧠 Asking LLM to generate code...")
    model = genai.GenerativeModel('models/gemini-pro-latest')

    if existing_code:
        prompt = f"""
        You are an expert web developer. Modify this HTML according to the following brief: "{brief}"

        EXISTING CODE:
        {existing_code}

        Return only the full, valid HTML — no explanations or markdown.
        """
    else:
        prompt = f"""
        You are an expert web developer. Based on this brief, generate a complete HTML page (index.html):

        BRIEF: "{brief}"

        Include all required HTML, CSS, and JavaScript. No explanations, only raw code.
        """

    response = model.generate_content(prompt)
    print("💡 LLM responded.")
    return response.text.replace("```html", "").replace("```", "").strip()


def generate_readme(task_data):
    """Generate README.md content."""
    return f"""
# Project: {task_data.get('task')}

## 📜 Summary
This project was automatically generated and deployed by an AI-based app for IITM BS (Tools in Data Science).

Brief: "{task_data.get('brief')}"

## ⚙️ How It Works
The Flask API receives a task, generates code using Gemini, creates a GitHub repo, and deploys it to GitHub Pages.

## 🪄 License
This project uses the MIT License. See LICENSE for details.
"""


def notify_evaluation_server(payload):
    """Notify evaluator with proxy-safe retry mechanism."""
    url = payload.pop("evaluation_url")
    print(f"📣 Notifying evaluation server at {url}...")

    delays = [1, 2, 4, 8]
    for i, delay in enumerate(delays):
        try:
            response = requests.post(url, json=payload, timeout=20)
            if response.status_code == 200:
                print("✅ Notification successful!")
                return True
            else:
                print(f"⚠️ Notification failed: {response.status_code} {response.text}")
        except requests.RequestException as e:
            print(f"🚨 Network issue: {e}")
        time.sleep(delay)
    print("❌ All notification attempts failed.")
    return False


def write_attachment(local_path, attachment):
    """Handle both text and binary attachments safely."""
    file_content = base64.b64decode(attachment['url'].split(',')[1])
    fname = attachment['name']
    mode = "wb" if fname.endswith((".xlsx", ".xls", ".png", ".jpg", ".zip")) else "w"
    with open(os.path.join(local_path, fname), mode) as f:
        if mode == "wb":
            f.write(file_content)
        else:
            f.write(file_content.decode("utf-8", errors="ignore"))


# --- CORE BACKGROUND TASK ---
def background_task(task_data):
    print(f"🤖 Task started: {task_data.get('task')} (Round {task_data.get('round')})")
    try:
        MY_SECRET = get_env_variable("MY_SECRET")
        GITHUB_USERNAME = get_env_variable("GITHUB_USERNAME")
        GITHUB_TOKEN = get_env_variable("GITHUB_TOKEN")

        if task_data.get("secret") != MY_SECRET:
            print("❌ Secret mismatch — aborting.")
            return

        repo_name = f"tds-proj-{task_data.get('task')}"
        repo_url = f"https://github.com/{GITHUB_USERNAME}/{repo_name}"
        local_path = os.path.join(os.getcwd(), repo_name)

        if os.path.exists(local_path):
            shutil.rmtree(local_path)
        os.makedirs(local_path)

        if task_data.get("round") == 1:
            print("--- ROUND 1: Creating new repo ---")
            html_code = generate_llm_code(task_data.get("brief"))
            readme = generate_readme(task_data)
            license_text = """MIT License

Copyright (c) 2025 Rahul Kumar

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
"""

            with open(os.path.join(local_path, "index.html"), "w", encoding="utf-8") as f:
                f.write(html_code)
            with open(os.path.join(local_path, "README.md"), "w", encoding="utf-8") as f:
                f.write(readme)
            with open(os.path.join(local_path, "LICENSE"), "w", encoding="utf-8") as f:
                f.write(license_text)

            for attachment in task_data.get("attachments", []):
                write_attachment(local_path, attachment)

            # --- Use GitHub REST API (no gh CLI) ---
            create_repo_cmd = (
                f'curl -L -X POST -H "Accept: application/vnd.github+json" '
                f'-H "Authorization: Bearer {GITHUB_TOKEN}" '
                f'-H "X-GitHub-Api-Version: 2022-11-28" '
                f'https://api.github.com/user/repos -d \'{{"name":"{repo_name}","private":false}}\''
            )

            commands = [
                'git config --global user.name "24f2001869"',
                'git config --global user.email "24f2001869@ds.study.iitm.ac.in"',
                "git config --global init.defaultBranch main",
                "git init",
                "git add .",
                'git commit -m \"feat: Initial commit for Round 1\"',
                create_repo_cmd,
                f"git remote add origin https://{GITHUB_USERNAME}:{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{repo_name}.git",
                "git push -u origin main",
                f'curl -L -X POST -H "Accept: application/vnd.github+json" '
                f'-H "Authorization: Bearer {GITHUB_TOKEN}" '
                f'-H "X-GitHub-Api-Version: 2022-11-28" '
                f'https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/pages '
                "-d '{\"source\":{\"branch\":\"main\",\"path\":\"/\"}}'"
            ]

        else:
            print("--- ROUND 2: Updating existing repo ---")
            subprocess.run(f"gh repo clone {repo_url} {local_path}", shell=True, check=True)
        
            # 🔐 Re-add authenticated remote (fixes push error)
            GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")
            GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
            subprocess.run(
                f"git remote set-url origin https://{GITHUB_USERNAME}:{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{repo_name}.git",
                shell=True,
                check=True,
                cwd=local_path
            )
        
            with open(os.path.join(local_path, "index.html"), "r", encoding="utf-8") as f:
                old_code = f.read()
            updated = generate_llm_code(task_data.get("brief"), existing_code=old_code)
            with open(os.path.join(local_path, "index.html"), "w", encoding="utf-8") as f:
                f.write(updated)
        
            commands = [
                "git add .",
                f'git commit -m "feat: Round {task_data.get("round")} update"',
                "git push"
            ]

    for cmd in commands:
        subprocess.run(cmd, shell=True, check=True, cwd=local_path)



        for cmd in commands:
            print(f"🔧 {cmd}")
            subprocess.run(cmd, shell=True, check=True, cwd=local_path)

        commit_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=local_path).decode().strip()
        pages_url = f"https://{GITHUB_USERNAME}.github.io/{repo_name}/"

        print("✅ GitHub Operations Complete")
        print(f"Repo: {repo_url}")
        print(f"Pages: {pages_url}")

        notify_evaluation_server({
            "email": task_data.get("email"),
            "task": task_data.get("task"),
            "round": task_data.get("round"),
            "nonce": task_data.get("nonce"),
            "repo_url": repo_url,
            "commit_sha": commit_sha,
            "pages_url": pages_url,
            "evaluation_url": task_data.get("evaluation_url")
        })

        shutil.rmtree(local_path)

    except Exception as e:
        print(f"❌ Background error: {e}")


# --- ROUTES ---

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "TDS Auto-Deployer running"}), 200


@app.route("/api-endpoint", methods=["GET", "HEAD"])
def api_alive():
    return jsonify({"status": "alive"}), 200


@app.route("/api-endpoint", methods=["POST"])
def handle_request():
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    data = request.json
    secret = data.get("secret")

    # ✅ Security check
    if secret != os.getenv("MY_SECRET"):
        print("❌ Invalid secret provided.")
        return jsonify({"error": "Invalid secret"}), 403

    print("🚀 Task request received.")
    threading.Thread(target=background_task, args=(data,)).start()
    return jsonify({"usercode": data.get("email")}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
