#!/usr/bin/env python3

import configparser
import re
import subprocess
from typing import Optional

import uvicorn
from fastapi import Depends, FastAPI, Form
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, validator

config = configparser.ConfigParser()
config.read("/etc/enrollment/config.ini")

HOST = config.get("server", "host", fallback="127.0.0.1")
PORT = config.getint("server", "port", fallback=8000)
DOMAIN = config.get("server", "domain", fallback="localhost")
USERNAME_REGEX = r"^[a-zA-Z0-9_.-]+$"
app = FastAPI()

form_html = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Registration Form</title>

<style>
  body {
    font-family: Arial, sans-serif;
    background: #f4f6f8;
    display: flex;
    justify-content: center;
    align-items: center;
    height: 100vh;
  }

  .container {
    background: white;
    padding: 30px;
    border-radius: 12px;
    box-shadow: 0 8px 20px rgba(0,0,0,0.1);
    width: 350px;
  }

  h1 {
    text-align: center;
    margin-bottom: 20px;
    font-size: 22px;
    color: #333;
  }

  label {
    font-size: 14px;
    color: #555;
    display: block;
    margin-top: 12px;
    margin-bottom: 6px;
  }

  input {
    width: 100%;
    padding: 10px;
    border: 1px solid #ddd;
    border-radius: 8px;
    font-size: 14px;
    outline: none;
    transition: 0.2s;
  }

  input:focus {
    border-color: #4a90e2;
    box-shadow: 0 0 4px rgba(74,144,226,0.3);
  }

  button {
    width: 100%;
    margin-top: 20px;
    padding: 10px;
    border: none;
    border-radius: 8px;
    background: #4a90e2;
    color: white;
    font-size: 15px;
    cursor: pointer;
    transition: 0.2s;
  }

  button:hover {
    background: #357abd;
  }

  pre {
    margin-top: 15px;
    background: #f0f0f0;
    padding: 10px;
    border-radius: 8px;
    font-size: 12px;
    overflow-x: auto;
  }
</style>
</head>

<body>

<div class="container">
  <h1>Create Sylk account</h1>

  <form method="post" action="/enrollment/user">
    
    <label>Display Name</label>
    <input type="text" name="displayname" required>

    <label>Username</label>
    <input type="text" name="username" required>

    <label>Password</label>
    <input type="password" name="password" required>

    <label>Email</label>
    <input type="email" name="email" required>

    <button type="submit">Sign Up</button>
  </form>

  <pre>{{output}}</pre>
</div>

</body>
</html>
"""


class UserForm(BaseModel):
    displayname: Optional[str] = Field(None, max_length=50)
    username: str = Field(..., min_length=1, max_length=30, regex=USERNAME_REGEX)
    password: str = Field(..., min_length=5, max_length=128)
    email: str = Field(..., regex=r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$')

    @validator("displayname", pre=True, always=True)
    def empty_to_none_and_check_length(cls, v):
        if v == "" or v is None:
            return None
        if len(v) < 1:
            raise ValueError("displayname must be at least 1 character")
        return v


def strip_ansi_codes(text: str) -> str:
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)



@app.get("/enrollment", response_class=HTMLResponse)
def read_form():
    return form_html.replace("{{output}}", "")


@app.post("/enrollment/user")
def create_user(
    displayname: Optional[str] = Form(None),
    username: str = Form(...),
    password: str = Form(...),
    email: str = Form(...),
):
    # FastAPI automatically validates these
    user = UserForm(
        displayname=displayname,
        username=username,
        password=password,
        email=email
    )

    sip_address = f"{user.username}@{DOMAIN}"
    cmd = ["opensips-cli", "-x", "user", "add", sip_address, user.password]

    result = subprocess.run(cmd, capture_output=True, text=True)
    output = strip_ansi_codes(result.stdout.strip())
    error = strip_ansi_codes(result.stderr.strip())

    if "ERROR" in output or "ERROR" in error:
        if "already exists" in output or "already exists" in error:
            return {
                "success": False,
                "sip_address": sip_address,
                "email": user.email,
                "error": "user_exists"
            }

        return {
            "success": False,
            "sip_address": sip_address,
            "email": user.email,
            "error": output or error
        }

    return {
        "success": True,
        "sip_address": sip_address,
        "email": user.email
    }


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
