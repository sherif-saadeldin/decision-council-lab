You are working inside the repo decision-council-lab.

Use uv only. Do not use pip.

Slice 1 (mock engine) is done. Build Slice 1.1: tighten CLI + docs + run artifact quality. Do not start OpenAI yet.

Run with:

uv run python main.py "Should I build a decision council engine as an internal tool first?"

Requirements:
- Use Pydantic models
- Use mock responses
- Save every run as JSON and Markdown
- Keep provider abstraction ready
- No frontend
- No Supabase
- No auth
- No n8n
