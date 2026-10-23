"""Guided Google Cloud console setup: step URLs, page snapshots, guarded clicks and typing.

The agent drives the console one step at a time: `goto` a step page, read the snapshot,
`click` or `fill` by visible label, read the snapshot again. Every action is checked first:
only Google Cloud console pages, never a sign-in or consent page (the human acts there),
never a destructive or billing control, never a password field.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

CONSOLE_HOST = "console.cloud.google.com"
HUMAN_ONLY_HOSTS = ("accounts.google.com", "myaccount.google.com", "payments.google.com", "pay.google.com")

STEPS: dict[str, dict[str, str]] = {
    "project": {"path": "/home/dashboard", "goal": "the selected project is the one whose ID is in the client JSON"},
    "api": {"path": "/apis/library/youtube.googleapis.com",
            "goal": "YouTube Data API v3 shows as enabled (a Manage button instead of Enable)"},
    "branding": {"path": "/auth/branding",
                 "goal": "app name, user support email and developer contact email are saved"},
    "audience": {"path": "/auth/audience",
                 "goal": "user type External and publishing status In production"},
    "clients": {"path": "/auth/clients",
                "goal": "a Desktop client exists and its JSON is downloaded"},
}
ORDER = ("project", "api", "branding", "audience", "clients")

# Controls the tool refuses to touch, matched against the control's visible text and label.
DENY = re.compile(
    r"\b(delete|remove|disable|shut\s*down|reset|revoke|rotate|billing|payment|upgrade|"
    r"back to testing|make internal|transfer|leave|unlink|clear)\b",
    re.IGNORECASE,
)
_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$")


class GuardError(RuntimeError):
    pass


def step_url(step: str, project_id: str) -> str:
    if step not in STEPS:
        raise ValueError(f"unknown step {step!r}; one of {list(ORDER)}")
    if not _PROJECT_ID.match(project_id or ""):
        raise ValueError(f"not a Google Cloud project ID: {project_id!r}")
    return f"https://{CONSOLE_HOST}{STEPS[step]['path']}?project={urllib.parse.quote(project_id)}"


def plan(project_id: str) -> list[dict[str, str]]:
    return [{"step": s, "url": step_url(s, project_id), "goal": STEPS[s]["goal"]} for s in ORDER]


def check_page(url: str) -> None:
    host = urllib.parse.urlsplit(url).hostname or ""
    if host in HUMAN_ONLY_HOSTS:
        raise GuardError(f"{host} is a sign-in or consent page; the human acts there, the tool does not")
    if host != CONSOLE_HOST:
        raise GuardError(f"refusing to act on {host or url!r}; only {CONSOLE_HOST} pages are driven")


def check_control(text: str) -> None:
    m = DENY.search(text or "")
    if m:
        raise GuardError(f"refusing to touch a control labelled {text.strip()[:80]!r} (matches {m.group(0)!r})")


def project_from_url(url: str) -> str | None:
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    values = q.get("project")
    return values[0] if values else None


# Read-only page summary. Only visible elements; no cookies, storage or input values of password fields.
_SNAPSHOT_JS = r"""() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const name = el => (el.getAttribute('aria-label') || el.innerText || el.value || '').replace(/\s+/g, ' ').trim();
  const uniq = arr => [...new Set(arr.filter(Boolean))];
  const headings = uniq([...document.querySelectorAll('h1,h2,h3,[role=heading]')].filter(vis).map(name)).slice(0, 25);
  const buttons = [...document.querySelectorAll('button,[role=button],a[role=button],input[type=submit]')]
    .filter(vis).map(el => ({text: name(el).slice(0, 80),
      disabled: !!(el.disabled || el.getAttribute('aria-disabled') === 'true')}))
    .filter(b => b.text);
  const seen = new Set(); const btns = [];
  for (const b of buttons) { const k = b.text + b.disabled; if (!seen.has(k)) { seen.add(k); btns.push(b); } }
  const fields = [...document.querySelectorAll('input,textarea,[role=combobox],[role=listbox],mat-select')]
    .filter(vis).filter(el => el.type !== 'hidden' && el.type !== 'password').map(el => {
      const id = el.id; const lab = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
      return {label: ((lab && lab.innerText) || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').trim().slice(0, 80),
              type: el.type || el.getAttribute('role') || el.tagName.toLowerCase(),
              value: (el.type === 'radio' || el.type === 'checkbox') ? el.checked : (el.value || el.innerText || '').trim().slice(0, 120)};
    }).slice(0, 40);
  const alerts = uniq([...document.querySelectorAll('[role=alert],[role=status],.cfc-message,.mat-mdc-snack-bar-label')]
    .filter(vis).map(name)).slice(0, 10);
  const dialogs = uniq([...document.querySelectorAll('[role=dialog],[role=alertdialog]')].filter(vis).map(el => name(el).slice(0, 300)));
  const main = document.querySelector('main,[role=main]') || document.body;
  return {title: document.title, headings, buttons: btns.slice(0, 60), fields, alerts, dialogs,
          text: (main.innerText || '').replace(/\n{3,}/g, '\n\n').slice(0, 2500)};
}"""


def snapshot(page) -> dict[str, Any]:
    parts = urllib.parse.urlsplit(page.url)
    info: dict[str, Any] = {"host": parts.hostname, "path": parts.path, "project": project_from_url(page.url)}
    if parts.hostname in HUMAN_ONLY_HOSTS:
        info["human_page"] = True
        info["title"] = page.title()
        return info  # do not scrape sign-in pages
    info.update(page.evaluate(_SNAPSHOT_JS))
    return info


_ROLES = ("button", "link", "menuitem", "option", "radio", "tab", "checkbox", "combobox")


def _visible(locator) -> list:
    return [locator.nth(i) for i in range(locator.count()) if locator.nth(i).is_visible()]


def find_control(page, text: str, role: str | None = None, exact: bool = True):
    """The single visible control with this accessible name. Ambiguity is an error, not a guess."""
    roles = (role,) if role else _ROLES
    for r in roles:
        hits = _visible(page.get_by_role(r, name=text, exact=exact))
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise GuardError(f"{len(hits)} visible {r} controls named {text!r}; be more specific with --role")
    if role is None:
        hits = _visible(page.get_by_text(text, exact=exact))
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise GuardError(f"{len(hits)} visible elements with text {text!r}; pass --role")
    raise GuardError(f"no visible control named {text!r}")


def click(page, text: str, role: str | None = None, exact: bool = True) -> dict[str, Any]:
    check_page(page.url)
    check_control(text)
    target = find_control(page, text, role, exact)
    label = " ".join(filter(None, [target.get_attribute("aria-label") or "", target.inner_text() or ""]))
    check_control(label)
    target.click(timeout=10000)
    page.wait_for_timeout(1500)
    return {"clicked": text}


def fill(page, label: str, value: str) -> dict[str, Any]:
    check_page(page.url)
    check_control(label)
    hits = _visible(page.get_by_label(label, exact=True))
    if len(hits) != 1:
        raise GuardError(f"{len(hits)} visible fields labelled {label!r}; need exactly one")
    field = hits[0]
    if (field.get_attribute("type") or "").lower() == "password":
        raise GuardError("refusing to type into a password field")
    field.fill(value, timeout=10000)
    return {"filled": label}
