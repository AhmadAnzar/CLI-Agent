# GENAI CLI Agent

A simple AI-powered (CLI) command line agent that uses LLMs to break down tasks and execute them automatically.

## What Does It Do?

This project lets you give instructions to an AI agent through your terminal. The agent:
- Thinks through problems step-by-step
- Uses available tools to get things done (read files, write files, run commands)
- Fetches website information including favicons
- Streams results back to your terminal in real-time

## How It Works

The system has two main parts:

1. **Backend (server.py)**: A FastAPI server that runs the AI agent loop
2. **CLI (cli.py)**: A command-line tool that sends instructions to the backend
3. **prompt.py**: The actual system prompt we are using. 
## Installation



### Setup

1. Clone this repository:
```bash
git clone <your-repo-url>
cd GENAI-CLI-AGENT
```


2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up your environment variables:
   - Create a `.env` file in the project root
   - Add your API credentials:
     ```
     OPENROUTER_API_KEY=your_key_here
     OPENROUTER_MODEL=your_model_name
     ```
     OR
     ```
     GROQ_API_KEY=your_key_here
     GROQ_MODEL=llama-3.3-70b-versatile
     ```

## Usage

### Start the Backend Server

In one terminal window:
```bash
uvicorn server:app --host 127.0.0.1 --port 8000
```

You should see:
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

### Run an Instruction

In another terminal window:
```bash
python cli.py "your instruction here"
```

### Example Commands

```bash
# Simple task
python cli.py "Say hello"

# Website fetching
python cli.py "Fetch information from https://example.com"

# File operations
python cli.py "Create a new file called test.txt with hello world in it"
```

## Available Tools for the Agent

The agent can use these tools:

- **listFiles**: See all files in the workspace
- **readFile**: Read the contents of a file
- **writeFile**: Create or update files
- **executeCommand**: Run shell commands
- **fetchWebsite**: Get website info including title, preview text, and favicon URL

## Project Structure

```
GENAI-CLI-AGENT/
├── server.py          # FastAPI backend with agent logic
├── cli.py             # Command-line interface
├── prompt.py          # System prompt for the AI agent
├── .env               # Your API keys (don't commit this!)
├── .gitignore         # Files to ignore in git
├── requirements.txt   # Python dependencies
└── README.md          # This file
```
