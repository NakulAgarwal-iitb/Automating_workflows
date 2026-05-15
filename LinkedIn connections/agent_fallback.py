"""
Agent-driven fallback for LinkedIn connections.
=================================================

When the inline "Connect" button isn't visible on a company People page
(common for 3rd-degree+ profiles where the action is hidden behind the
"More" menu on the profile page itself), this module hands the work off
to a Claude-driven `browser-use` agent.

The agent navigates: Profile URL -> "More" button -> "Connect" ->
"Add a note" -> types a personalized note -> "Send".

It connects to the SAME Chrome instance as the main Selenium script via
the Chrome DevTools Protocol (CDP) on port 9222, so your LinkedIn login
is shared and you don't need to sign in twice.

Requirements:
    pip install "browser-use"
    export ANTHROPIC_API_KEY=...
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class AgentResult:
    profile_url: str
    name: str
    success: bool
    detail: str = ""
    raw: str = field(default="", repr=False)


# Task prompt fed to the LLM. Keeping it explicit but tolerant of LinkedIn UI variations.
_TASK_TEMPLATE = """\
You are sending ONE LinkedIn connection request with a personalized note.

Target profile URL: {profile_url}
Recipient first name: {name}

Follow these steps precisely:

1. Navigate to the profile URL. Wait until the page is fully loaded
   (you can see the person's name, headline, and action buttons in the
   top profile section).

2. In the top profile section (the area near the photo and headline),
   locate the action buttons. You will typically see "Message", "Follow",
   and a "More" button. The "More" button can appear as:
      - the word "More" with a small down-arrow, or
      - three horizontal dots ("...").

3. Click the "More" button to open its dropdown menu.

4. In the dropdown, find an item labeled "Connect" and click it.
   - If you don't see "Connect" but see "Personalize invite", click that.
   - If you see only "Follow" / "Pending" / "Message" / "Remove connection"
     and no Connect option at all, STOP and report `connect_not_available`.

5. A "How do you know <Name>?" / "Add a note" dialog should appear.
   Click the "Add a note" button. If a note textarea is already shown,
   skip this step.

6. In the message textarea, type EXACTLY the following note. Do NOT
   paraphrase, summarize, or shorten it. Type it verbatim:
\"\"\"
{note}
\"\"\"

7. Click the button labeled "Send", "Send invitation", or "Send now".

8. Confirm the dialog closed and the request was sent.

Hard rules:
- If LinkedIn asks for the recipient's email address before connecting,
  STOP and report `email_required` (do NOT guess an email).
- If you see a CAPTCHA, security check, or "Verify it's you" page,
  STOP and report `captcha`.
- If a Premium / upsell modal pops up, dismiss it (close button or "X")
  and continue.
- The note has a 300-character LinkedIn limit. The note above is already
  trimmed, just type it as given.
- Do not navigate away from the profile until the request is sent.
- Do not open extra tabs unless needed.

When finished, output ONE of these status strings as your final answer:
  success
  connect_not_available
  email_required
  captcha
  failed: <short reason>
"""


async def connect_via_agent(
    profile_url: str,
    name: str,
    note: str,
    cdp_port: int = 9222,
    model: str = "claude-sonnet-4-0",
    max_steps: int = 25,
) -> AgentResult:
    """Use browser-use + Claude to send a connection request to a single profile.

    Returns an AgentResult with `success=True` only when the agent reports
    `success` as its final answer.
    """
    try:
        from browser_use import Agent, Browser, ChatAnthropic
    except ImportError as e:
        return AgentResult(
            profile_url,
            name,
            success=False,
            detail=(
                "browser-use is not installed. Install with:\n"
                "  pip install browser-use\n"
                f"(import error: {e})"
            ),
        )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return AgentResult(
            profile_url,
            name,
            success=False,
            detail="ANTHROPIC_API_KEY env var is not set.",
        )

    if len(note) > 300:
        note = note[:297] + "..."

    task = _TASK_TEMPLATE.format(profile_url=profile_url, name=name, note=note)

    try:
        browser = Browser(cdp_url=f"http://localhost:{cdp_port}")
        llm = ChatAnthropic(model=model, temperature=0.0)
        agent = Agent(task=task, llm=llm, browser=browser)
        history = await agent.run(max_steps=max_steps)
    except Exception as e:  # noqa: BLE001
        return AgentResult(
            profile_url, name, success=False, detail=f"agent runtime error: {e!s}"
        )

    final = None
    for attr in ("final_result", "result"):
        fn = getattr(history, attr, None)
        if callable(fn):
            try:
                final = fn()
                break
            except Exception:  # noqa: BLE001
                pass
    if final is None:
        final = str(history)

    final_text = str(final).strip()
    lowered = final_text.lower()
    success = "success" in lowered and "connect_not_available" not in lowered and "failed" not in lowered

    return AgentResult(
        profile_url=profile_url,
        name=name,
        success=success,
        detail=final_text[:300] if final_text else "(no result returned)",
        raw=final_text,
    )


async def connect_batch_via_agent(
    targets: List[Tuple[str, str]],
    note_template: str,
    cdp_port: int = 9222,
    model: str = "claude-sonnet-4-0",
    delay_seconds: float = 5.0,
    max_steps: int = 25,
) -> List[AgentResult]:
    """Run the agent sequentially over a list of (profile_url, first_name) pairs."""
    results: List[AgentResult] = []
    total = len(targets)
    for i, (url, name) in enumerate(targets, 1):
        note = note_template.format(name=name)
        print(f"   🤖 [{i}/{total}] {name}  →  {url}")
        res = await connect_via_agent(
            url, name, note, cdp_port=cdp_port, model=model, max_steps=max_steps
        )
        results.append(res)
        marker = "✅" if res.success else "⏭️ "
        print(f"      {marker} {res.detail[:140]}")
        if i < total:
            await asyncio.sleep(delay_seconds)
    return results
