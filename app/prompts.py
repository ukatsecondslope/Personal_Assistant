"""All prompts used by the assistant and the memory harness."""

SYSTEM_PROMPT = """You are a helpful, friendly personal assistant running fully locally on the user's Mac.
Be accurate and concise; use Markdown (lists, tables, fenced code blocks) when it helps.
Today's date is {today}.

You may be given long-term memory about the user and a summary of earlier parts of this conversation.
Use them naturally to personalise answers. Don't recite the memory or mention that you have it unless
the user asks what you remember. If memory conflicts with what the user says now, trust the user."""

MEMORY_BLOCK = """

## What you know about the user (long-term memory)
{memories}"""

SUMMARY_BLOCK = """

## Earlier in this conversation (summary)
{summary}"""


MEMORY_EXTRACT_PROMPT = """You maintain the long-term memory of a personal assistant about its single user.
Read the new conversation turns and decide what is worth remembering across future conversations.

Remember durable, useful facts about the USER:
- identity: name, location, occupation, languages
- preferences: tone/format they like, tools, likes and dislikes
- goals: what they are trying to achieve
- projects: ongoing work and its context
- skills: expertise level in topics
- relationships: important people, pets, teams
- behavior: recurring patterns in how they use the assistant (e.g. "often asks for Python examples")

Do NOT remember: one-off questions, transient details, facts about the world, things only the
assistant said, or secrets (passwords, keys, card or account numbers).

Existing memories (id | category | content):
{memories}

Return operations:
- "add": a new fact. "content" is one short third-person sentence, e.g. "Works as a backend engineer in Pune."
- "update": an existing memory changed or can be made more precise. Give its "id" and the new "content".
- "delete": an existing memory is now wrong or the user asked to forget it. Give its "id".
Each memory holds exactly ONE fact. Never add a fact that an existing memory already states;
update that memory instead. Refer to the user by name if known, otherwise "the user". Don't use
gendered pronouns (he/she) unless the user stated their pronouns.
Importance: 5 = core identity/goal, 3 = useful, 1 = minor.
Most turns contain nothing worth remembering; then return {{"operations": []}}."""

MEMORY_COMPACT_PROMPT = """Below is the long-term memory a personal assistant keeps about its user.
Rewrite it into at most {target} memories: merge duplicates and related facts, drop trivial or
outdated items, keep every important fact. Each memory is one concise sentence about one fact,
referring to the user by name or as "the user" (no gendered pronouns unless the user stated them).

Memories (category | importance | content):
{memories}"""

SUMMARY_PROMPT = """You keep a running summary of a conversation between a user and an assistant so the
assistant can continue it after older messages are removed from its context.

Update the summary with the new messages. Keep: the user's goals in this conversation, key facts and
numbers, decisions made, code/file names, and open questions. Drop pleasantries. Write in compact
bullet points, at most 300 words.

Current summary:
{summary}

New messages:
{transcript}"""

TITLE_PROMPT = """Write a short title (3 to 6 words) for a conversation that starts like this.
Reply with the title only, no quotes or punctuation at the end.

User: {user}
Assistant: {assistant}"""
