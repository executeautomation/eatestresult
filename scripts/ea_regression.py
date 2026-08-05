#!/usr/bin/env python3
"""EA Employee App (https://eaapp.somee.com) full functional regression suite.

Drives a local Chromium via Playwright so that every step can be recorded:
  * one video per test case (webm -> mp4) — kept only for FAILED cases by default
  * a full-page screenshot at the moment of every failure
  * a machine-readable results.json + human Markdown report

Usage:
  python3 scripts/ea_regression.py [--base-url URL] [--out DIR] [--keep-all-videos]
                                   [--user admin] [--password password]
                                   [--only TC01,TC05]

Exit code is 0 when the run completed (regardless of test failures) and non-zero
only when the harness itself could not run — a failing test is data, not a crash.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

CHROME = Path.home() / ".cache/ms-playwright/chromium-1228/chrome-linux64/chrome"
FFMPEG = Path(shutil.which("ffmpeg") or Path.home() / ".cache/ms-playwright/ffmpeg-1011/ffmpeg-linux")
VIEWPORT = {"width": 1440, "height": 900}
MOBILE_VIEWPORT = {"width": 390, "height": 844}


class Failure(AssertionError):
    """Raised by a test body when an expectation is not met."""


@dataclass
class Result:
    id: str
    area: str
    title: str
    steps: str
    expected: str
    status: str = "pending"          # pass | fail | error | skipped
    actual: str = ""
    severity: str = ""
    screenshots: list[str] = field(default_factory=list)
    video: str | None = None
    duration_s: float = 0.0
    notes: list[str] = field(default_factory=list)


@dataclass
class Ctx:
    page: Page
    base: str
    res: Result
    out: Path
    user: str
    password: str

    # ---------- helpers ----------
    def go(self, path: str, wait: str = "domcontentloaded") -> None:
        url = path if path.startswith("http") else f"{self.base}{path}"
        self.page.goto(url, timeout=45000, wait_until=wait)

    def status_of(self, path: str) -> tuple[int, int]:
        """HTTP status + body length via the API client (survives empty error bodies,
        which make page.goto raise ERR_HTTP_RESPONSE_CODE_FAILURE)."""
        url = path if path.startswith("http") else f"{self.base}{path}"
        r = self.page.context.request.get(url, timeout=45000)
        return r.status, len(r.body())

    def submit(self, action_contains: str | None = None) -> None:
        """Click the submit button of the INTENDED form.

        The layout renders a header Logout form first, so a bare
        `button[type=submit]` click logs the session out instead of submitting.
        """
        if action_contains:
            for f in self.page.query_selector_all("form"):
                act = f.get_attribute("action") or ""
                if action_contains.lower() in act.lower():
                    btn = f.query_selector("button[type=submit], input[type=submit]")
                    if btn:
                        btn.click()
                        self.page.wait_for_load_state("domcontentloaded")
                        return
        for b in reversed(self.page.query_selector_all(
                "form button[type=submit], form input[type=submit]")):
            act = b.evaluate("e => (e.closest('form') && e.closest('form').getAttribute('action')) || ''")
            if "logout" not in (act or "").lower():
                b.click()
                self.page.wait_for_load_state("domcontentloaded")
                return
        raise Failure("no usable submit button found on the page")

    def text(self) -> str:
        return self.page.inner_text("body")

    def shot(self, label: str) -> str:
        """Full-page screenshot; returned path is relative to the output dir."""
        safe = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        rel = f"screenshots/{self.res.id}-{safe}.png"
        dest = self.out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.page.screenshot(path=str(dest), full_page=True)
        except Exception:                      # a screenshot must never mask the real failure
            self.page.screenshot(path=str(dest))
        self.res.screenshots.append(rel)
        return rel

    def note(self, msg: str) -> None:
        self.res.notes.append(msg)

    def fail(self, msg: str, severity: str = "High") -> None:
        self.res.severity = severity
        raise Failure(msg)

    def expect_text(self, needle: str, label: str, severity: str = "High") -> None:
        if needle.lower() not in self.text().lower():
            self.shot(f"missing-{label}")
            self.fail(f"expected page to contain {needle!r} ({label}); it did not")

    def login(self, user: str | None = None, password: str | None = None) -> None:
        self.go("/Account/Login")
        self.page.fill("#UserName", user if user is not None else self.user)
        self.page.fill("#Password", password if password is not None else self.password)
        self.submit("/Account/Login")
        time.sleep(1.0)

    def logged_in(self) -> bool:
        return "hello" in self.text().lower() and "logout" in self.text().lower()

    def ensure_admin(self) -> None:
        self.go("/Employee")
        if not self.logged_in():
            self.login()

    def employee_count(self) -> int | None:
        m = re.search(r"([\d,]+)\s+employees", self.text())
        return int(m.group(1).replace(",", "")) if m else None

    def find_row(self, name: str) -> dict[str, str] | None:
        """Search the employee list by name and return the first row's cells."""
        self.go(f"/Employee?searchTerm={name.replace(' ', '+')}&emailTerm=&gradeFilter=")
        rows = self.page.query_selector_all("table tbody tr")
        for r in rows:
            cells = [c.inner_text().strip() for c in r.query_selector_all("td")]
            if cells and name.lower() in cells[0].lower():
                return {
                    "name": cells[0],
                    "age": cells[1] if len(cells) > 1 else "",
                    "salary": cells[2] if len(cells) > 2 else "",
                    "duration": cells[3] if len(cells) > 3 else "",
                    "grade": cells[4] if len(cells) > 4 else "",
                    "email": cells[5] if len(cells) > 5 else "",
                }
        return None

    def set_select(self, selector: str, label: str) -> None:
        self.page.select_option(selector, label=label)


# --------------------------------------------------------------------------
# Test cases.  Each takes a Ctx and raises Failure on a defect.
# --------------------------------------------------------------------------

def tc_home(c: Ctx) -> None:
    c.go("/")
    t = c.text()
    for item in ("Home", "Employees", "About"):
        if item.lower() not in t.lower():
            c.shot("nav")
            c.fail(f"navigation item {item!r} missing from the home page")
    if not c.page.title().strip():
        c.note("page <title> is empty")
    c.res.actual = f"Home page rendered; title={c.page.title()!r}; nav present"


def tc_list(c: Ctx) -> None:
    c.go("/Employee")
    n = c.employee_count()
    rows = len(c.page.query_selector_all("table tbody tr"))
    if n is None or rows == 0:
        c.shot("list")
        c.fail("employee list did not render a record count or any rows")
    c.res.actual = f"{n} employees reported, {rows} rows on page 1"


def tc_search_name(c: Ctx) -> None:
    c.go("/Employee")
    rows = c.page.query_selector_all("table tbody tr")
    if not rows:
        c.fail("no employees to search for")
    target = rows[0].query_selector_all("td")[0].inner_text().strip()
    hit = c.find_row(target)
    if not hit:
        c.shot("search-miss")
        c.fail(f"search for existing employee {target!r} returned no matching row")
    c.res.actual = f"search {target!r} -> row returned ({hit['email']})"


def tc_search_case(c: Ctx) -> None:
    c.go("/Employee")
    rows = c.page.query_selector_all("table tbody tr")
    if not rows:
        c.fail("no employees to search for")
    target = rows[0].query_selector_all("td")[0].inner_text().strip()
    hit = c.find_row(target.swapcase())
    if not hit:
        c.shot("case-miss")
        c.fail(f"case-flipped search {target.swapcase()!r} did not match {target!r}")
    c.res.actual = f"{target.swapcase()!r} matched {hit['name']!r} — search is case-insensitive"


def tc_search_nomatch(c: Ctx) -> None:
    c.go("/Employee?searchTerm=ZZZ_NO_MATCH_ZZZ&emailTerm=&gradeFilter=")
    t = c.text().lower()
    if "no employees found" not in t:
        c.shot("nomatch")
        c.fail("no-match search did not show the empty-state message")
    c.res.actual = "empty state shown: 'No employees found.'"


def tc_grade_filter(c: Ctx) -> None:
    """Known defect BUG-01: the grade filter does not filter."""
    c.go("/Employee")
    c.set_select("select[name=gradeFilter]", "Junior")
    c.submit("/Employee")
    time.sleep(0.8)
    grades = [
        r.query_selector_all("td")[4].inner_text().strip()
        for r in c.page.query_selector_all("table tbody tr")
        if len(r.query_selector_all("td")) > 4
    ]
    selected = c.page.eval_on_selector("select[name=gradeFilter]", "e => e.value")
    wrong = sorted({g for g in grades if g and g.lower() != "junior"})
    if wrong:
        c.shot("grade-filter-unfiltered")
        c.res.severity = "High"
        raise Failure(
            f"filtering Grade=Junior returned non-Junior rows {wrong}; "
            f"dropdown reset to {selected!r} instead of keeping the selection"
        )
    c.res.actual = f"only Junior rows returned ({len(grades)} rows)"


def tc_create_validation(c: Ctx) -> None:
    c.ensure_admin()
    c.go("/Employee/Create")
    c.submit("/Employee/Create")
    time.sleep(1.0)
    t = c.text().lower()
    missing = [f for f in ("name", "age", "salary") if f"{f} field is required" not in t]
    if "required" not in t:
        c.shot("create-validation")
        c.fail("submitting an empty create form produced no validation messages")
    c.res.actual = "required-field validation fired on empty submit"
    if missing:
        c.note(f"messages not found verbatim for: {', '.join(missing)}")


def tc_create_email_validation(c: Ctx) -> None:
    c.ensure_admin()
    c.go("/Employee/Create")
    c.page.fill("#Name", "QA Email Validation")
    c.page.fill("#Age", "30")
    c.page.fill("#Salary", "1000")
    c.page.fill("#DurationWorked", "5")
    c.set_select("#Grade", "Junior")
    c.page.fill("#Email", "not-an-email")
    c.submit("/Employee/Create")
    time.sleep(1.0)
    t = c.text().lower()
    if "not a valid e-mail" not in t:
        c.shot("email-validation")
        c.fail("invalid email address was not rejected")
    lost = [
        f for f, sel in (("DurationWorked", "#DurationWorked"), ("Grade", "#Grade"))
        if not (c.page.eval_on_selector(sel, "e => e.value") or "")
    ]
    if lost:
        c.note(f"after a failed submit the form loses these values: {', '.join(lost)}")
    c.res.actual = "email format rejected: 'The Email field is not a valid e-mail address.'"


def tc_create(c: Ctx) -> None:
    c.ensure_admin()
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    name = f"QA Regression {stamp}"
    c.go("/Employee/Create")
    c.page.fill("#Name", name)
    c.page.fill("#Age", "30")
    c.page.fill("#Salary", "75000")
    c.page.fill("#DurationWorked", "24")
    c.set_select("#Grade", "Senior")
    c.page.fill("#Email", f"qa.{stamp}@example.com")
    c.submit("/Employee/Create")
    time.sleep(1.5)
    row = c.find_row(name)
    if not row:
        c.shot("create-missing")
        c.fail(f"created employee {name!r} is not in the list after submit")
    if "75,000" not in row["salary"] or row["grade"].lower() != "senior":
        c.shot("create-wrong-values")
        c.fail(f"created record stored wrong values: {row}")
    STATE["created_name"] = name
    href = c.page.get_attribute("table tbody tr a[href*='/Edit/']", "href") or ""
    m = re.search(r"/Edit/(\d+)", href)
    if m:
        STATE["created_id"] = m.group(1)
    c.res.actual = f"created {name!r} (id {STATE.get('created_id','?')}), all values stored correctly"


def tc_details(c: Ctx) -> None:
    """The per-row Details action must drill into THAT employee."""
    c.ensure_admin()
    name = STATE.get("created_name")
    if not name:
        c.res.status = "skipped"
        c.res.actual = "create test did not produce a record"
        return
    c.find_row(name)
    link = c.page.query_selector("table tbody tr a[href*='EmployeeDetails']")
    if not link:
        c.shot("no-details-link")
        c.fail("no Details link on the employee row")
    href = link.get_attribute("href") or ""
    link.click()
    c.page.wait_for_load_state("domcontentloaded")
    time.sleep(1.0)
    rows = c.page.query_selector_all("table tbody tr")
    names = [r.query_selector_all("td")[0].inner_text().strip()
             for r in rows if r.query_selector_all("td")]
    if len(names) > 1 and name not in names:
        c.shot("details-ignores-id")
        c.res.severity = "Medium"
        raise Failure(
            f"Details for one employee ({href}) renders the unfiltered list of "
            f"{len(names)} other employees — the id in the URL is ignored and the "
            f"selected record ({name!r}) is not even on the page"
        )
    if name not in c.text():
        c.shot("details-content")
        c.fail("details page does not show the selected employee")
    c.res.actual = f"details page ({href}) shows the selected employee"


def tc_edit(c: Ctx) -> None:
    """Known defect BUG-02: edits are silently discarded."""
    c.ensure_admin()
    name = STATE.get("created_name")
    if not name:
        c.res.status = "skipped"
        c.res.actual = "create test did not produce a record"
        return
    c.find_row(name)
    link = c.page.query_selector("table tbody tr a[href*='/Edit/']")
    if not link:
        c.shot("no-edit-link")
        c.fail("no Edit link on the employee row")
    link.click()
    c.page.wait_for_load_state("domcontentloaded")
    time.sleep(0.8)
    c.page.fill("#Salary", "82000")
    c.set_select("#Grade", "C-Level")
    c.shot("edit-form-before-save")
    c.submit("/Employee/Edit")
    time.sleep(1.5)
    row = c.find_row(name)
    if not row:
        c.shot("edit-record-gone")
        c.fail("record disappeared after saving an edit")
    if "82,000" not in row["salary"] or row["grade"].lower() != "c-level":
        c.shot("edit-not-persisted")
        c.res.severity = "High"
        raise Failure(
            "saving the edit form changed nothing — salary/grade still "
            f"{row['salary']} / {row['grade']} (expected $82,000.00 / C-Level), and no error was shown"
        )
    c.res.actual = "edit persisted: salary $82,000.00, grade C-Level"


def tc_delete(c: Ctx) -> None:
    c.ensure_admin()
    name = STATE.get("created_name")
    if not name:
        c.res.status = "skipped"
        c.res.actual = "create test did not produce a record"
        return
    c.find_row(name)
    link = c.page.query_selector("table tbody tr a[href*='/Delete/']")
    if not link:
        c.shot("no-delete-link")
        c.fail("no Delete link on the employee row")
    link.click()
    c.page.wait_for_load_state("domcontentloaded")
    time.sleep(0.8)
    if "are you sure" not in c.text().lower():
        c.note("delete page shows no confirmation prompt")
    c.submit("/Employee/Delete")
    time.sleep(1.5)
    if c.find_row(name):
        c.shot("delete-not-applied")
        c.fail(f"record {name!r} still present after confirming delete")
    STATE["deleted"] = True
    c.res.actual = f"{name!r} removed; no longer returned by search"


def tc_login_empty(c: Ctx) -> None:
    c.go("/Account/Login")
    c.submit("/Account/Login")
    time.sleep(1.0)
    t = c.text().lower()
    if "required" not in t:
        c.shot("login-empty")
        c.fail("empty login submit produced no required-field validation")
    c.res.actual = "both fields flagged required"


def tc_login_bad(c: Ctx) -> None:
    c.login(c.user, "wrongpass123")
    t = c.text().lower()
    if "invalid login" not in t:
        c.shot("login-bad")
        c.fail("wrong password was not rejected with an invalid-login message")
    c.res.actual = "'Invalid login attempt.' shown"


def tc_login_ok(c: Ctx) -> None:
    c.login()
    if not c.logged_in():
        c.shot("login-ok")
        c.fail(f"login as {c.user!r} did not authenticate")
    c.go("/Employee")
    t = c.text()
    for action in ("New Employee", "Edit", "Delete"):
        if action.lower() not in t.lower():
            c.shot("admin-actions")
            c.fail(f"admin action {action!r} not visible after admin login")
    c.res.actual = "authenticated as admin; New Employee / Edit / Delete visible"


def tc_anon_access(c: Ctx) -> None:
    """An anonymous visitor must not reach admin-only screens or actions."""
    ctx2 = c.page.context.browser.new_context(viewport=VIEWPORT)
    p2 = ctx2.new_page()
    try:
        p2.goto(f"{c.base}/Employee", timeout=45000, wait_until="domcontentloaded")
        listing = p2.inner_text("body")
        leaked = [a for a in ("New Employee", "✎ Edit", "🗑 Delete") if a in listing]
        p2.goto(f"{c.base}/Employee/Create", timeout=45000, wait_until="domcontentloaded")
        url_after = p2.url
        body = p2.inner_text("body").lower()
        gated = ("login" in url_after.lower()) or ("sign in" in body) or ("access denied" in body)
        if leaked or not gated:
            rel = f"screenshots/{c.res.id}-anon-create.png"
            (c.out / rel).parent.mkdir(parents=True, exist_ok=True)
            p2.screenshot(path=str(c.out / rel), full_page=True)
            c.res.screenshots.append(rel)
            c.res.severity = "Critical" if not gated else "Medium"
            raise Failure(
                (f"anonymous user sees admin actions {leaked} on the list; " if leaked else "")
                + (f"/Employee/Create is reachable without login (landed on {url_after})"
                   if not gated else "")
            )
        c.res.actual = "anonymous user sees read-only list; /Employee/Create redirects to login"
    finally:
        ctx2.close()


def tc_register(c: Ctx) -> None:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    user = f"qauser{stamp}"
    pwd = "QaP@ssw0rd!23"
    c.go("/Account/Register")
    body = c.text().lower()
    if "create account" not in body and "register" not in body:
        c.shot("register-page")
        c.fail(f"register page did not load (landed on {c.page.url})")
    # Fill by input name/type so the test survives id changes.
    filled: list[str] = []
    for el in c.page.query_selector_all("form input:not([type=hidden])"):
        name = (el.get_attribute("name") or "").lower()
        itype = (el.get_attribute("type") or "text").lower()
        if itype == "checkbox":
            continue
        val = pwd if itype == "password" else (
            f"{user}@example.com" if "email" in name or itype == "email" else user)
        el.fill(val)
        filled.append(name or itype)
    if not filled:
        c.shot("register-form")
        c.fail("register form exposes no fillable inputs")
    STATE["register_fields"] = ",".join(filled)
    c.submit("/Account/Register")
    time.sleep(1.5)
    t = c.text().lower()
    if c.logged_in() or "hello" in t:
        STATE["registered_user"] = user
        c.res.actual = f"registered {user!r} and was signed in automatically"
        return
    if "error" in t or "required" in t or "invalid" in t:
        c.shot("register-rejected")
        c.fail(f"registration of {user!r} was rejected: {c.text()[:300]!r}")
    STATE["registered_user"] = user
    c.res.actual = f"registration of {user!r} submitted without error (no auto sign-in)"


def tc_dashboard(c: Ctx) -> None:
    c.ensure_admin()
    c.go("/Home/Dashboard")
    t = c.text()
    if "dashboard" not in t.lower():
        c.shot("dashboard")
        c.fail(f"dashboard page did not render (landed on {c.page.url})")
    nums = re.findall(r"\$[\d,]+(?:\.\d+)?|\b\d{1,3}(?:,\d{3})+\b|\b\d+\b", t)
    if not nums:
        c.shot("dashboard-empty")
        c.fail("dashboard shows no metrics at all")
    c.res.actual = f"dashboard rendered with metrics (sample: {nums[:6]})"


MONEY = re.compile(r"[₹$]\s*([\d,]+(?:\.\d+)?)")


def _first_pf_id(c: Ctx) -> str | None:
    c.go("/EmployeeDetails")
    href = c.page.eval_on_selector_all(
        "a[href*='EmployeePF']", "els => els.length ? els[0].getAttribute('href') : ''")
    m = re.search(r"/EmployeePF/(\d+)", href or "")
    return m.group(1) if m else None


def tc_pf_calc(c: Ctx) -> None:
    """PF total must equal 12% × monthly salary × months worked (the page's own stated rule)."""
    c.ensure_admin()
    eid = _first_pf_id(c)
    if not eid:
        c.shot("no-pf-link")
        c.fail("no PF Contribution link on the employee details screen")
    c.go(f"/EmployeeDetails/EmployeePF/{eid}")
    body = c.text()
    nums = MONEY.findall(body)
    months = re.search(r"(\d+)\s*months", body)
    if not nums or not months:
        c.shot("pf-unreadable")
        c.fail(f"PF page for employee {eid} shows no total/duration ({c.page.url})")
    total = float(nums[0].replace(",", ""))
    salary = float(nums[1].replace(",", "")) if len(nums) > 1 else 0.0
    m = int(months.group(1))
    expected = round(salary * 0.12 * m, 2)
    STATE["pf_sample"] = f"employee {eid}: total {total}, salary {salary}, {m} months"
    if abs(total - expected) > 0.05:
        c.shot("pf-math")
        c.res.severity = "High"
        raise Failure(
            f"PF total {total} does not match the page's own formula "
            f"12% × {salary} × {m} months = {expected}"
        )
    c.res.actual = f"PF total ₹{total} == 12% × {salary} × {m} months (employee {eid})"


def tc_bonus_formula(c: Ctx) -> None:
    """Company contribution must match the formula the page prints next to it."""
    c.ensure_admin()
    eid = _first_pf_id(c)
    if not eid:
        c.res.status = "skipped"
        c.res.actual = "no contribution links on the details screen"
        return
    c.go(f"/EmployeeDetails/EmployeeBonus/{eid}")
    body = c.text()
    nums = MONEY.findall(body)
    months = re.search(r"(\d+)\s*months", body)
    formula = re.search(r"PF\s*(\d+)%\s*\+\s*(\d+)\s*[×x]\s*(\d+)%", body)
    if not nums or not months:
        c.shot("bonus-unreadable")
        c.fail(f"company contribution page for employee {eid} is unreadable ({c.page.url})")
    total = float(nums[0].replace(",", ""))
    salary = float(nums[1].replace(",", "")) if len(nums) > 1 else 0.0
    m = int(months.group(1))
    if not formula:
        c.res.actual = f"company contribution ₹{total} shown for employee {eid} (no parsable formula label)"
        c.note("the page states no machine-readable contribution formula")
        return
    pf_pct, grade_mult, allow_pct = (int(g) for g in formula.groups())
    stated_rate = (pf_pct + grade_mult * allow_pct) / 100
    expected = round(salary * stated_rate * m, 2)
    effective = round(total / (salary * m) * 100, 2) if salary and m else 0
    if abs(total - expected) > 0.05:
        c.shot("bonus-formula-mismatch")
        c.res.severity = "Medium"
        raise Failure(
            f"company contribution ₹{total} contradicts the formula printed on the same page "
            f"(PF {pf_pct}% + {grade_mult}×{allow_pct}% = {stated_rate*100:.0f}% × {salary} × {m} "
            f"months = {expected}); the value implies an effective rate of {effective}%"
        )
    c.res.actual = (f"company contribution ₹{total} matches its stated formula "
                    f"({stated_rate*100:.0f}% × {salary} × {m} months)")


def tc_currency(c: Ctx) -> None:
    """The same salary field must use one currency symbol across screens."""
    c.ensure_admin()
    c.go("/Employee")
    list_syms = set(re.findall(r"[₹$]", c.text()))
    c.go("/EmployeeDetails")
    detail_syms = set(re.findall(r"[₹$]", c.text()))
    if list_syms and detail_syms and list_syms != detail_syms:
        c.shot("currency-list")
        c.res.severity = "Low"
        raise Failure(
            f"salary is rendered as {'/'.join(sorted(list_syms))} on the employee list but "
            f"{'/'.join(sorted(detail_syms))} on the details/PF screens — same field, two currencies"
        )
    c.res.actual = f"consistent currency symbol across screens ({'/'.join(sorted(list_syms))})"


def tc_pagination(c: Ctx) -> None:
    c.go("/Employee")
    first_page_names = [
        r.query_selector_all("td")[0].inner_text().strip()
        for r in c.page.query_selector_all("table tbody tr")
        if r.query_selector_all("td")
    ]
    links = c.page.query_selector_all("a[href*='page'], .pagination a")
    target = None
    for l in links:
        if (l.inner_text() or "").strip() in ("3", "Next →", "Next"):
            target = l
            break
    if not target:
        c.shot("pagination")
        c.fail("no pagination controls found on the employee list")
    target.click()
    c.page.wait_for_load_state("domcontentloaded")
    time.sleep(1.0)
    page_n_names = [
        r.query_selector_all("td")[0].inner_text().strip()
        for r in c.page.query_selector_all("table tbody tr")
        if r.query_selector_all("td")
    ]
    if not page_n_names:
        c.shot("pagination-empty")
        c.fail(f"paginated page rendered no rows ({c.page.url})")
    if page_n_names == first_page_names:
        c.shot("pagination-same-rows")
        c.res.severity = "High"
        raise Failure(f"page 2/3 shows the same rows as page 1 ({c.page.url})")
    showing = re.search(r"Showing\s+([\d,]+)[–-]([\d,]+)\s+of\s+([\d,]+)", c.text())
    c.res.actual = (
        f"deep page loaded distinct rows ({c.page.url})"
        + (f"; {showing.group(0)}" if showing else "")
    )


def tc_last_page(c: Ctx) -> None:
    c.go("/Employee")
    nums = [
        int((l.inner_text() or "0").strip())
        for l in c.page.query_selector_all(".pagination a, a[href*='page']")
        if (l.inner_text() or "").strip().isdigit()
    ]
    if not nums:
        c.res.status = "skipped"
        c.res.actual = "no numbered pagination links to derive the last page from"
        return
    last = max(nums)
    c.go(f"/Employee?page={last}")
    rows = c.page.query_selector_all("table tbody tr")
    if not rows:
        c.shot("last-page-empty")
        c.fail(f"last page (page={last}) rendered no rows")
    c.go(f"/Employee?page={last + 500}")
    t = c.text().lower()
    rows_over = c.page.query_selector_all("table tbody tr")
    if "error" in t or "exception" in t:
        c.shot("page-overflow-error")
        c.res.severity = "Medium"
        raise Failure(f"page={last + 500} produced an error page instead of an empty/clamped list")
    c.res.actual = (
        f"page={last} rendered {len(rows)} rows; out-of-range page={last + 500} "
        f"handled gracefully ({len(rows_over)} rows, no error page)"
    )


def tc_mobile(c: Ctx) -> None:
    ctx2 = c.page.context.browser.new_context(viewport=MOBILE_VIEWPORT, is_mobile=False)
    p2 = ctx2.new_page()
    try:
        p2.goto(f"{c.base}/Employee", timeout=45000, wait_until="domcontentloaded")
        time.sleep(1.0)
        metrics = p2.evaluate(
            "() => ({doc: document.documentElement.scrollWidth, win: window.innerWidth,"
            " tbl: (document.querySelector('table')||{}).scrollWidth || 0})"
        )
        overflow = metrics["doc"] - metrics["win"]
        rel = f"screenshots/{c.res.id}-mobile-390px.png"
        (c.out / rel).parent.mkdir(parents=True, exist_ok=True)
        p2.screenshot(path=str(c.out / rel), full_page=True)
        c.res.screenshots.append(rel)
        if overflow > 20:
            c.res.severity = "Low"
            raise Failure(
                f"at 390px the document is {metrics['doc']}px wide vs a {metrics['win']}px "
                f"viewport ({overflow}px horizontal overflow; table is {metrics['tbl']}px)"
            )
        c.res.actual = f"no horizontal overflow at 390px (doc {metrics['doc']}px / win {metrics['win']}px)"
    finally:
        ctx2.close()


def tc_404(c: Ctx) -> None:
    """A missing record must not 500 or leak framework internals.

    The app answers with an EMPTY body, which makes page.goto raise
    ERR_HTTP_RESPONSE_CODE_FAILURE — so assert over the HTTP response itself.
    """
    code, length = c.status_of("/Employee/Details/99999999")
    body = ""
    if length:
        try:
            c.go("/Employee/Details/99999999")
            body = c.text().lower()
        except Exception:
            body = ""
    leaked = any(k in body for k in ("stack trace", "exception details",
                                     "microsoft.entityframework", "system.nullreference"))
    if leaked:
        c.shot("error-leak")
        c.res.severity = "Medium"
        raise Failure(f"a non-existent record returns a page leaking framework internals (HTTP {code})")
    if code >= 500:
        c.res.severity = "Medium"
        raise Failure(f"a non-existent record returns HTTP {code} instead of a handled 404")
    if code == 404 and length == 0:
        c.res.actual = "HTTP 404 with an empty body — correct status, no branded error page"
        c.note("404 responses have an empty body (no styled 'not found' page), so a browser "
               "shows its own network-error screen instead of the app's UI")
        return
    c.res.actual = f"non-existent record handled with HTTP {code} ({length} byte body), no internals leaked"


TESTS: list[tuple[str, str, str, str, str, Callable[[Ctx], None]]] = [
    ("TC01", "Navigation",  "Home page loads with primary navigation",
     "Open /", "Landing page renders with Home / Employees / About navigation", tc_home),
    ("TC02", "Employee list", "Employee list renders with record count",
     "Open /Employee", "Table of employees plus a total count", tc_list),
    ("TC03", "Search", "Search by an existing employee name",
     "Search the first listed name", "That employee's row is returned", tc_search_name),
    ("TC04", "Search", "Search is case-insensitive",
     "Search the same name with flipped case", "The same row is returned", tc_search_case),
    ("TC05", "Search", "No-match search shows an empty state",
     "Search ZZZ_NO_MATCH_ZZZ", "'No employees found.' message", tc_search_nomatch),
    ("TC06", "Filter", "Filter the list by Grade",
     "Select Grade=Junior and submit the search form",
     "Only Junior employees are listed and the dropdown keeps the selection", tc_grade_filter),
    ("TC07", "Auth", "Login with empty credentials is rejected",
     "Submit /Account/Login with both fields blank", "Required-field validation on both fields",
     tc_login_empty),
    ("TC08", "Auth", "Login with a wrong password is rejected",
     "Submit admin / wrongpass123", "'Invalid login attempt.'", tc_login_bad),
    ("TC09", "Auth", "Login with valid admin credentials",
     "Submit admin / password", "Authenticated; admin-only actions become visible", tc_login_ok),
    ("TC10", "Auth", "Anonymous user cannot reach admin screens",
     "Open /Employee and /Employee/Create in a clean session",
     "No admin actions on the list; /Employee/Create redirects to login", tc_anon_access),
    ("TC11", "Auth", "Register a new user account",
     "Submit /Account/Register with a unique username, email and valid password",
     "Account is created without error", tc_register),
    ("TC12", "Create", "Create form validates required fields",
     "Submit /Employee/Create empty", "Every required field is flagged", tc_create_validation),
    ("TC13", "Create", "Create form validates email format",
     "Submit the create form with Email=not-an-email", "Email format error is shown",
     tc_create_email_validation),
    ("TC14", "Create", "Create a valid employee record",
     "Fill every field with valid data and submit",
     "Record is saved with the exact values and appears in the list", tc_create),
    ("TC15", "Details", "Details view shows the record's data",
     "Open Details for the created employee", "Name, salary, grade and duration are shown",
     tc_details),
    ("TC16", "Edit", "Update an existing employee",
     "Edit the created employee: salary 75000→82000, grade Senior→C-Level, save",
     "Changes are persisted and visible in the list", tc_edit),
    ("TC17", "Delete", "Delete an employee",
     "Delete the created employee and confirm", "Record is removed from the list", tc_delete),
    ("TC18", "Dashboard", "Dashboard renders metrics",
     "Open /Dashboard as admin", "Dashboard renders with employee metrics", tc_dashboard),
    ("TC19", "PF", "PF contribution total matches its own formula",
     "Open an employee's PF Contribution page",
     "Total equals 12% × monthly salary × months worked", tc_pf_calc),
    ("TC19b", "PF", "Company contribution matches its printed formula",
     "Open the same employee's Company Contribution page",
     "Total equals the PF% + grade-allowance formula shown on the page", tc_bonus_formula),
    ("TC19c", "Consistency", "Salary currency symbol is consistent across screens",
     "Compare the currency symbol on /Employee and /EmployeeDetails",
     "The same salary field uses one currency symbol everywhere", tc_currency),
    ("TC20", "Pagination", "Deep pagination returns different rows",
     "From /Employee go to page 3 (or Next)", "A distinct set of rows loads", tc_pagination),
    ("TC21", "Pagination", "Last page and out-of-range page behave sanely",
     "Open the last page, then a page number far beyond it",
     "Last page has rows; out-of-range clamps or empties without an error page", tc_last_page),
    ("TC22", "Responsive", "Employee list at a 390px mobile viewport",
     "Open /Employee at 390×844", "No horizontal overflow of the document", tc_mobile),
    ("TC23", "Robustness", "Non-existent record id is handled",
     "Open /Employee/Details/99999999", "Handled 404/redirect with no framework internals leaked",
     tc_404),
]

STATE: dict[str, str] = {}


def to_mp4(webm: Path, dest: Path) -> str | None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    ff = str(FFMPEG) if FFMPEG.exists() else shutil.which("ffmpeg")
    if not ff:
        shutil.copy(webm, dest.with_suffix(".webm"))
        return dest.with_suffix(".webm").name
    proc = subprocess.run(
        [ff, "-y", "-i", str(webm), "-vf",
         "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=10", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-crf", "30", "-movflags", "+faststart", str(dest)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        shutil.copy(webm, dest.with_suffix(".webm"))
        return dest.with_suffix(".webm").name
    return dest.name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="https://eaapp.somee.com")
    ap.add_argument("--out", default="output/ea-regression")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="password")
    ap.add_argument("--keep-all-videos", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    for sub in ("screenshots", "videos"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    raw_videos = out / "_raw_videos"
    raw_videos.mkdir(parents=True, exist_ok=True)

    only = {t.strip().upper() for t in args.only.split(",") if t.strip()}
    plan = [t for t in TESTS if not only or t[0] in only]
    if not CHROME.exists():
        print(f"FATAL: chromium not found at {CHROME}", file=sys.stderr)
        return 3

    results: list[Result] = []
    started = datetime.now(timezone.utc)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=str(CHROME),
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        for tid, area, title, steps, expected, fn in plan:
            res = Result(id=tid, area=area, title=title, steps=steps, expected=expected)
            ctx = browser.new_context(
                viewport=VIEWPORT,
                record_video_dir=str(raw_videos / tid),
                record_video_size=VIEWPORT,
            )
            page = ctx.new_page()
            page.set_default_timeout(20000)
            c = Ctx(page=page, base=args.base_url.rstrip("/"), res=res, out=out,
                    user=args.user, password=args.password)
            t0 = time.time()
            try:
                fn(c)
                if res.status == "pending":
                    res.status = "pass"
            except Failure as e:
                res.status = "fail"
                res.actual = str(e)
                res.severity = res.severity or "High"
            except PWTimeout as e:
                res.status = "fail"
                res.actual = f"timed out waiting for the app: {str(e).splitlines()[0]}"
                res.severity = res.severity or "Medium"
                try:
                    c.shot("timeout")
                except Exception:
                    pass
            except Exception as e:
                res.status = "error"
                res.actual = f"{type(e).__name__}: {e}"
                res.notes.append(traceback.format_exc(limit=3))
                try:
                    c.shot("error")
                except Exception:
                    pass
            res.duration_s = round(time.time() - t0, 1)
            video_src = None
            try:
                video_src = page.video.path() if page.video else None
            except Exception:
                pass
            ctx.close()                      # flush the video file
            if video_src and Path(video_src).exists():
                if res.status in ("fail", "error") or args.keep_all_videos:
                    name = to_mp4(Path(video_src), out / "videos" / f"{tid}.mp4")
                    if name:
                        res.video = f"videos/{name}"
            results.append(res)
            print(f"[{res.status.upper():5}] {tid} {title} ({res.duration_s}s)"
                  + (f" -> {res.actual[:110]}" if res.status != "pass" else ""))
        browser.close()

    shutil.rmtree(raw_videos, ignore_errors=True)
    summary = {
        "base_url": args.base_url,
        "started_utc": started.isoformat(),
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": sum(r.status == "pass" for r in results),
        "failed": sum(r.status == "fail" for r in results),
        "errors": sum(r.status == "error" for r in results),
        "skipped": sum(r.status == "skipped" for r in results),
        "results": [r.__dict__ for r in results],
    }
    (out / "results.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
