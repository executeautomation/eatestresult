#!/usr/bin/env python3
"""EA Employee App — extended (Lead-QA) test layers.

Loaded by ea_regression.py as EXTRA_TESTS and executed by the same runner, so
every test here gets the same isolated browser context, video and failure
screenshot treatment as the core functional suite.

Layers: authorization/RBAC · session & cookie security · transport & headers ·
CSRF · injection & XSS · input boundaries · account management · business-logic
integrity · HTTP/perf/links · accessibility & UX.

IMPORTANT: this module never imports from ea_regression. The runner executes
that file as __main__, so importing it would create a second copy of Failure
whose raises would be recorded as harness errors instead of test failures.
Everything is done through the Ctx handed to each test (c.fail / c.shot /
c.note / c.go / c.submit / ...).
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

XSTATE: dict[str, Any] = {}
PWD = "QaP@ssw0rd!23"
KEY_PAGES = ["/", "/Employee", "/Account/Login", "/Account/Register", "/EmployeeDetails"]


# ---------------------------------------------------------------- helpers
def _stamp() -> str:
    return datetime.now().strftime("%H%M%S%f")[:10]


def _api(c: Any, path: str, method: str = "GET", **kw: Any):
    url = path if path.startswith("http") else f"{c.base}{path}"
    return c.page.context.request.fetch(url, method=method, timeout=45000, **kw)


def _headers(c: Any, path: str = "/") -> dict[str, str]:
    r = _api(c, path)
    return {k.lower(): v for k, v in r.headers.items()}


def _logout(c: Any) -> bool:
    """Sign out through the header's logout form. Returns True when the UI confirms it.

    /Account/LogOff answers GET with an empty error body (ERR_HTTP_RESPONSE_CODE_FAILURE),
    so it must never be navigated to directly.
    """
    for _ in range(2):
        for f in c.page.query_selector_all("form"):
            act = (f.get_attribute("action") or "").lower()
            if "logoff" in act or "logout" in act:
                btn = f.query_selector("button[type=submit], input[type=submit]")
                if btn:
                    btn.click()
                    c.page.wait_for_load_state("domcontentloaded")
                    time.sleep(0.8)
                    break
        if not c.logged_in():
            return True
        c.go("/Employee")
    if c.logged_in():
        c.page.context.clear_cookies()          # last resort: drop the client-side session
        c.go("/Employee")
    return not c.logged_in()


def _register(c: Any, user: str, pwd: str = PWD) -> str:
    """Fill the register form generically (field names differ across builds)."""
    c.go("/Account/Register")
    for el in c.page.query_selector_all("form input:not([type=hidden])"):
        name = (el.get_attribute("name") or "").lower()
        itype = (el.get_attribute("type") or "text").lower()
        if itype == "checkbox":
            continue
        el.fill(pwd if itype == "password"
                else (f"{user}@example.com" if "email" in name or itype == "email" else user))
    c.submit("/Account/Register")
    time.sleep(1.5)
    return c.text()


def _test_user(c: Any) -> tuple[str, str]:
    """A registered NON-admin account, created once per run and reused."""
    if XSTATE.get("user"):
        return XSTATE["user"], XSTATE["pwd"]
    user = f"qarole{_stamp()}"
    _register(c, user)
    XSTATE["user"], XSTATE["pwd"] = user, PWD
    return user, PWD


def _as_test_user(c: Any) -> tuple[str, str]:
    user, pwd = _test_user(c)
    _logout(c)
    c.login(user, pwd)
    return user, pwd


def _admin_create(c: Any, **over: Any) -> str:
    """Create an employee as admin and return its name."""
    c.ensure_admin()
    name = over.pop("name", f"QA Temp {_stamp()}")
    c.go("/Employee/Create")
    c.page.fill("#Name", name)
    c.page.fill("#Age", str(over.pop("age", 33)))
    c.page.fill("#Salary", str(over.pop("salary", 50000)))
    c.page.fill("#DurationWorked", str(over.pop("duration", 12)))
    c.set_select("#Grade", over.pop("grade", "Junior"))
    c.page.fill("#Email", over.pop("email", f"qa{_stamp()}@example.com"))
    c.submit("/Employee/Create")
    time.sleep(1.4)
    return name


def _id_of(c: Any, name: str) -> str | None:
    c.find_row(name)
    href = c.page.get_attribute("table tbody tr a[href*='/Edit/']", "href") or ""
    m = re.search(r"/Edit/(\d+)", href)
    return m.group(1) if m else None


def _delete_by_name(c: Any, name: str) -> bool:
    """Best-effort cleanup as admin; returns True when the record is gone."""
    try:
        c.ensure_admin()
        if not c.find_row(name):
            return True
        link = c.page.query_selector("table tbody tr a[href*='/Delete/']")
        if not link:
            return False
        link.click()
        c.page.wait_for_load_state("domcontentloaded")
        time.sleep(0.6)
        c.submit("/Employee/Delete")
        time.sleep(1.2)
        return not c.find_row(name)
    except Exception:
        return False


def _grades(c: Any) -> list[str]:
    return [r.query_selector_all("td")[4].inner_text().strip()
            for r in c.page.query_selector_all("table tbody tr")
            if len(r.query_selector_all("td")) > 4]


# ---------------------------------------------------------------- RBAC
def sec_nonadmin_list(c: Any) -> None:
    user, _ = _as_test_user(c)
    c.go("/Employee")
    t = c.text()
    if not c.logged_in():
        c.res.status = "skipped"
        c.res.actual = f"could not sign in as the non-admin account {user!r}"
        return
    leaked = [a for a in ("New Employee", "Edit", "Delete") if a.lower() in t.lower()]
    if leaked:
        c.shot("nonadmin-sees-admin-actions")
        c.res.severity = "High"
        c.fail(f"a plain registered user ({user}) sees admin-only actions {leaked} on the "
               "employee list — the UI does not distinguish roles", "High")
    c.res.actual = f"non-admin {user!r} sees a read-only list (no admin actions)"


def sec_nonadmin_create_page(c: Any) -> None:
    user, _ = _as_test_user(c)
    c.go("/Employee/Create")
    url, body = c.page.url.lower(), c.text().lower()
    gated = "login" in url or "denied" in body or "forbidden" in body
    if not gated and c.page.query_selector("#Name"):
        c.shot("nonadmin-create-form")
        c.res.severity = "High"
        c.fail(f"the create-employee form is served to a plain registered user ({user}) — "
               "no role check on /Employee/Create", "High")
    c.res.actual = f"/Employee/Create is not served to the non-admin account ({c.page.url})"


def sec_nonadmin_create_post(c: Any) -> None:
    """Privilege escalation: can a non-admin actually persist a record?"""
    user, _ = _as_test_user(c)
    c.go("/Employee/Create")
    if not c.page.query_selector("#Name"):
        c.res.actual = "non-admin cannot even load the create form, so no write was attempted"
        return
    name = f"QA PrivEsc {_stamp()}"
    c.page.fill("#Name", name)
    c.page.fill("#Age", "30")
    c.page.fill("#Salary", "1234")
    c.page.fill("#DurationWorked", "3")
    c.set_select("#Grade", "Junior")
    c.page.fill("#Email", f"privesc{_stamp()}@example.com")
    c.submit("/Employee/Create")
    time.sleep(1.5)
    created = bool(c.find_row(name))
    if created:
        c.shot("nonadmin-created-record")
        _delete_by_name(c, name)
        c.res.severity = "Critical"
        c.fail(f"a plain registered user ({user}) created employee record {name!r} — "
               "any self-registered visitor can write to the employee database", "Critical")
    c.res.actual = "non-admin submit did not persist a record"


def sec_nonadmin_delete(c: Any) -> None:
    """IDOR / privilege escalation on the destructive action."""
    name = _admin_create(c, name=f"QA DelProbe {_stamp()}")
    eid = _id_of(c, name)
    if not eid:
        _delete_by_name(c, name)
        c.res.status = "skipped"
        c.res.actual = "could not seed a record to probe with"
        return
    user, _ = _as_test_user(c)
    try:
        c.go(f"/Employee/Delete/{eid}")
        url, body = c.page.url.lower(), c.text().lower()
        gated = "login" in url or "denied" in body or "forbidden" in body
        if not gated and c.page.query_selector("form[action*='Delete']"):
            c.submit("/Employee/Delete")
            time.sleep(1.3)
            gone = not c.find_row(name)
            if gone:
                c.shot("nonadmin-deleted-record")
                c.res.severity = "Critical"
                c.fail(f"a plain registered user ({user}) deleted employee id {eid} "
                       f"({name!r}) — destructive actions are not role-protected", "Critical")
            c.shot("nonadmin-delete-page")
            c.res.severity = "High"
            c.fail(f"the delete-confirmation page for id {eid} is served to a non-admin "
                   f"user ({user}), exposing another user's record", "High")
        c.res.actual = f"/Employee/Delete/{eid} is not served to the non-admin account"
    finally:
        _delete_by_name(c, name)


def sec_anon_write(c: Any) -> None:
    """An unauthenticated POST must never create data."""
    name = f"QA AnonPost {_stamp()}"
    r = _api(c, "/Employee/Create", "POST", form={
        "Name": name, "Age": "30", "Salary": "999", "DurationWorked": "2",
        "Grade": "1", "Email": f"anon{_stamp()}@example.com",
    })
    time.sleep(1.0)
    created = bool(_delete_by_name(c, name) is False or c.find_row(name))
    if c.find_row(name):
        c.shot("anon-write")
        _delete_by_name(c, name)
        c.res.severity = "Critical"
        c.fail(f"an unauthenticated POST to /Employee/Create (HTTP {r.status}) created "
               f"record {name!r} — writes require no session at all", "Critical")
    c.res.actual = f"unauthenticated POST rejected (HTTP {r.status}); no record created"


def sec_anon_dashboard(c: Any) -> None:
    code, length = c.status_of("/Home/Dashboard")
    r = _api(c, "/Home/Dashboard")
    body = r.text().lower() if length else ""
    exposed = ("dashboard" in body and ("total" in body or "employee" in body)
               and "login" not in r.url.lower())
    if exposed:
        c.go("/Home/Dashboard")
        c.shot("anon-dashboard")
        c.res.severity = "Medium"
        c.fail("the management dashboard and its aggregate salary figures render for an "
               f"anonymous visitor (HTTP {code}, landed on {r.url})", "Medium")
    c.res.actual = f"dashboard not exposed anonymously (HTTP {code}, {r.url})"


def sec_manage_requires_auth(c: Any) -> None:
    r = _api(c, "/Manage")
    final = r.url.lower()
    body = r.text().lower() if r.status < 400 else ""
    gated = "login" in final or r.status in (401, 403) or "log in" in body or "sign in" in body
    if not gated:
        c.go("/Manage")
        c.shot("anon-manage")
        c.res.severity = "High"
        c.fail(f"the account management screen is reachable anonymously (HTTP {r.status}, {r.url})",
               "High")
    c.res.actual = f"/Manage requires authentication (HTTP {r.status} -> {r.url})"


RBAC_TESTS = [
    ("SEC01", "Authorization", "Plain registered user sees no admin actions",
     "Register a new user, sign in, open /Employee",
     "Create / Edit / Delete actions are hidden from non-admin users", sec_nonadmin_list),
    ("SEC02", "Authorization", "Non-admin cannot open the create form",
     "As the registered non-admin user, open /Employee/Create",
     "Access is denied or redirected, not the form", sec_nonadmin_create_page),
    ("SEC03", "Authorization", "Non-admin cannot persist a new employee",
     "As the non-admin user, submit the create form",
     "The write is rejected; no record is created", sec_nonadmin_create_post),
    ("SEC04", "Authorization", "Non-admin cannot delete an employee",
     "Seed a record as admin, then open/submit its Delete page as the non-admin user",
     "The destructive action is refused for non-admins", sec_nonadmin_delete),
    ("SEC05", "Authorization", "Unauthenticated POST cannot create data",
     "POST employee fields to /Employee/Create with no session",
     "Request is rejected (redirect/401/403) and nothing is stored", sec_anon_write),
    ("SEC06", "Authorization", "Dashboard is not exposed anonymously",
     "Request /Home/Dashboard with no session",
     "Anonymous visitors cannot read aggregate payroll metrics", sec_anon_dashboard),
    ("SEC07", "Authorization", "Account management requires a session",
     "Request /Manage with no session", "Redirect to login or 401/403",
     sec_manage_requires_auth),
]


# ---------------------------------------------------------------- session & transport
def _auth_cookie(c: Any) -> dict | None:
    for ck in c.page.context.cookies():
        if "auth" in ck["name"].lower() or "identity" in ck["name"].lower():
            return ck
    return None


def sec_cookie_flags(c: Any) -> None:
    c.login()
    ck = _auth_cookie(c)
    if not ck:
        c.res.status = "skipped"
        c.res.actual = "no authentication cookie found after login"
        return
    missing = []
    if not ck.get("httpOnly"):
        missing.append("HttpOnly")
    if not ck.get("secure"):
        missing.append("Secure")
    if (ck.get("sameSite") or "None") in ("None", "none"):
        missing.append("SameSite")
    XSTATE["auth_cookie_name"] = ck["name"]
    if missing:
        c.res.severity = "High" if "HttpOnly" in missing else "Medium"
        c.fail(f"the authentication cookie {ck['name']!r} is set without {', '.join(missing)} "
               f"(flags: httpOnly={ck.get('httpOnly')}, secure={ck.get('secure')}, "
               f"sameSite={ck.get('sameSite')})", c.res.severity)
    c.res.actual = f"auth cookie {ck['name']!r} is HttpOnly + Secure + SameSite={ck.get('sameSite')}"


def sec_session_fixation(c: Any) -> None:
    c.go("/Account/Login")
    before = {ck["name"]: ck["value"] for ck in c.page.context.cookies()}
    c.login()
    after = {ck["name"]: ck["value"] for ck in c.page.context.cookies()}
    if not c.logged_in():
        c.res.status = "skipped"
        c.res.actual = "admin login did not authenticate; fixation check not meaningful"
        return
    reused = [k for k, v in before.items() if k in after and after[k] == v
              and ("session" in k.lower() or "auth" in k.lower())]
    if reused:
        c.res.severity = "Medium"
        c.fail(f"session cookie(s) {reused} keep the same value across the login boundary — "
               "a pre-authentication session identifier is not rotated (fixation risk)", "Medium")
    c.res.actual = ("a new authentication cookie is issued at login; "
                    f"pre-login cookies: {sorted(before)} -> post-login: {sorted(after)}")


def sec_logout_invalidates(c: Any) -> None:
    """The cookie captured before logout must not work afterwards."""
    c.login()
    ck = _auth_cookie(c)
    if not ck:
        c.res.status = "skipped"
        c.res.actual = "no authentication cookie to replay"
        return
    stolen = {"name": ck["name"], "value": ck["value"], "domain": ck["domain"],
              "path": ck.get("path", "/")}
    # The finding is only meaningful if the sign-out itself really happened through the UI.
    clicked = False
    for f in c.page.query_selector_all("form"):
        if "logoff" in (f.get_attribute("action") or "").lower() or \
                "logout" in (f.get_attribute("action") or "").lower():
            btn = f.query_selector("button[type=submit], input[type=submit]")
            if btn:
                btn.click()
                c.page.wait_for_load_state("domcontentloaded")
                time.sleep(1.0)
                clicked = True
                break
    if not clicked or c.logged_in():
        c.res.status = "skipped"
        c.res.actual = ("could not complete a UI sign-out, so a cookie-replay verdict would be "
                        "unsafe")
        return
    ctx2 = c.page.context.browser.new_context()
    try:
        ctx2.add_cookies([stolen])
        p2 = ctx2.new_page()
        p2.goto(f"{c.base}/Employee/Create", timeout=45000, wait_until="domcontentloaded")
        replayed_in = bool(p2.query_selector("#Name")) and "login" not in p2.url.lower()
        if replayed_in:
            rel = f"screenshots/{c.res.id}-cookie-replay.png"
            (c.out / rel).parent.mkdir(parents=True, exist_ok=True)
            p2.screenshot(path=str(c.out / rel), full_page=True)
            c.res.screenshots.append(rel)
            c.res.severity = "High"
            c.fail("the authentication cookie still grants access to the admin create form "
                   "after logout — signing out does not invalidate the session server-side",
                   "High")
        c.res.actual = "the pre-logout cookie no longer authenticates after logout"
    finally:
        ctx2.close()


def sec_no_cache_authenticated(c: Any) -> None:
    c.login()
    r = _api(c, "/Employee/Create")
    h = {k.lower(): v for k, v in r.headers.items()}
    cache = (h.get("cache-control") or "").lower()
    ok = any(k in cache for k in ("no-store", "no-cache"))
    if not ok:
        c.res.severity = "Low"
        c.fail("authenticated admin pages are served without a no-store/no-cache directive "
               f"(Cache-Control: {h.get('cache-control') or 'absent'}) — the browser can "
               "re-display them from cache after logout", "Low")
    c.res.actual = f"authenticated pages send Cache-Control: {h.get('cache-control')}"


def sec_headers(c: Any) -> None:
    h = _headers(c, "/Employee")
    want = {
        "x-content-type-options": "MIME-sniffing protection",
        "x-frame-options|content-security-policy": "clickjacking protection",
        "strict-transport-security": "HTTPS enforcement (HSTS)",
        "referrer-policy": "referrer leakage control",
        "content-security-policy": "content injection control",
    }
    missing = []
    for key, why in want.items():
        if not any(k in h for k in key.split("|")):
            missing.append(f"{key.replace('|', ' / ')} ({why})")
    XSTATE["resp_headers"] = h
    if missing:
        c.res.severity = "Medium"
        c.fail("responses carry none of the standard browser-hardening headers — missing: "
               + "; ".join(missing), "Medium")
    c.res.actual = "all baseline security headers present"


def sec_tech_disclosure(c: Any) -> None:
    h = XSTATE.get("resp_headers") or _headers(c, "/Employee")
    leaks = {k: v for k, v in h.items()
             if k in ("server", "x-powered-by", "x-aspnet-version", "x-aspnetmvc-version")}
    if leaks:
        c.res.severity = "Low"
        c.fail("the server advertises its stack in response headers: "
               + ", ".join(f"{k}: {v}" for k, v in leaks.items())
               + " — version disclosure helps an attacker target known CVEs", "Low")
    c.res.actual = "no server/framework version headers disclosed"


def sec_https_redirect(c: Any) -> None:
    http_url = c.base.replace("https://", "http://")
    try:
        r = _api(c, f"{http_url}/Employee")
    except Exception as e:
        c.res.actual = f"plain HTTP is not reachable at all ({type(e).__name__}) — effectively HTTPS-only"
        return
    ended_https = r.url.lower().startswith("https://")
    if not ended_https:
        c.res.severity = "High"
        c.fail(f"the site answers over plain HTTP without redirecting to HTTPS "
               f"(HTTP {r.status} at {r.url}) — credentials can be submitted in clear text", "High")
    c.res.actual = f"plain HTTP is redirected to HTTPS ({r.url})"


def sec_antiforgery(c: Any) -> None:
    """State-changing forms need an anti-forgery token, and POSTs without it must fail."""
    c.ensure_admin()
    c.go("/Employee/Create")
    has_token = bool(c.page.query_selector("input[name='__RequestVerificationToken']"))
    name = f"QA CSRF {_stamp()}"
    r = _api(c, "/Employee/Create", "POST", form={
        "Name": name, "Age": "31", "Salary": "4321", "DurationWorked": "4",
        "Grade": "1", "Email": f"csrf{_stamp()}@example.com",
    })
    time.sleep(1.0)
    created = bool(c.find_row(name))
    if created:
        _delete_by_name(c, name)
    if not has_token and created:
        c.res.severity = "High"
        c.fail("the create form carries no anti-forgery token and a cross-site POST with only "
               f"the session cookie created record {name!r} (HTTP {r.status}) — the app is "
               "open to cross-site request forgery", "High")
    if not has_token:
        c.res.severity = "Medium"
        c.fail("state-changing forms render without an anti-forgery token "
               "(no __RequestVerificationToken input on /Employee/Create)", "Medium")
    if created:
        c.res.severity = "High"
        c.fail(f"a POST without the anti-forgery token still created record {name!r} "
               f"(HTTP {r.status}) — the token is issued but not validated", "High")
    c.res.actual = f"anti-forgery token present and enforced (token-less POST -> HTTP {r.status}, nothing stored)"


def sec_user_enumeration(c: Any) -> None:
    c.login("definitely_no_such_user_zz", "whatever123")
    unknown = c.text().lower()
    c.login(c.user, "definitely_the_wrong_password_zz")
    wrong = c.text().lower()
    def msg(t: str) -> str:
        m = re.search(r"(invalid[^.<]*\.|user[^.<]*not[^.<]*\.|incorrect[^.<]*\.)", t)
        return (m.group(1) if m else t[:80]).strip()
    a, b = msg(unknown), msg(wrong)
    if a != b:
        c.shot("user-enumeration")
        c.res.severity = "Medium"
        c.fail(f"login responses differ for an unknown user ({a!r}) versus a wrong password "
               f"({b!r}) — the form lets an attacker enumerate valid usernames", "Medium")
    c.res.actual = f"identical rejection for unknown user and wrong password ({a!r})"


def sec_lockout(c: Any) -> None:
    for _ in range(6):
        c.login(c.user, "wrong_password_attempt")
    after = c.text().lower()
    locked = "locked" in after or "too many" in after or "try again" in after
    c.login()
    still_ok = c.logged_in()
    if not locked and still_ok:
        c.res.severity = "Medium"
        c.fail("six consecutive failed logins for the admin account triggered no lockout, "
               "throttling or CAPTCHA, and the account remained usable immediately after — "
               "credential stuffing is unthrottled", "Medium")
    c.res.actual = ("account lockout/throttling engaged after repeated failures"
                    if locked else "failures throttled (admin login blocked afterwards)")


def sec_weak_password(c: Any) -> None:
    user = f"qaweak{_stamp()}"
    c.go("/Account/Register")
    for el in c.page.query_selector_all("form input:not([type=hidden])"):
        name = (el.get_attribute("name") or "").lower()
        itype = (el.get_attribute("type") or "text").lower()
        if itype == "checkbox":
            continue
        el.fill("1" if itype == "password"
                else (f"{user}@example.com" if "email" in name or itype == "email" else user))
    c.submit("/Account/Register")
    time.sleep(1.5)
    body = c.text().lower()
    rejected = ("must be at least" in body or "password" in body and "error" in body
                or "invalid" in body or "required" in body)
    if c.logged_in() and not rejected:
        c.shot("weak-password-accepted")
        c.res.severity = "High"
        c.fail(f"registration accepted the one-character password '1' for {user!r} — "
               "no password complexity policy is enforced", "High")
    c.res.actual = "a one-character password is rejected by the registration policy"


def sec_duplicate_user(c: Any) -> None:
    user, pwd = _test_user(c)
    _logout(c)
    body = _register(c, user, pwd)
    low = body.lower()
    taken = "already taken" in low or "already exists" in low or "duplicate" in low
    if not taken and c.logged_in():
        c.shot("duplicate-username")
        c.res.severity = "High"
        c.fail(f"registering the existing username {user!r} a second time was accepted — "
               "usernames are not unique", "High")
    c.res.actual = f"duplicate username {user!r} rejected by registration"


SESSION_TESTS = [
    ("SEC08", "Session", "Authentication cookie carries HttpOnly/Secure/SameSite",
     "Sign in as admin and inspect the auth cookie flags",
     "Cookie is HttpOnly, Secure and SameSite-scoped", sec_cookie_flags),
    ("SEC09", "Session", "Session identifier is rotated at login",
     "Capture cookies before and after signing in",
     "A pre-auth session id is not reused after authentication", sec_session_fixation),
    ("SEC10", "Session", "Logout invalidates the session server-side",
     "Capture the auth cookie, log out, replay the cookie in a clean browser",
     "The replayed cookie no longer reaches admin screens", sec_logout_invalidates),
    ("SEC11", "Session", "Authenticated pages are not cacheable",
     "Inspect Cache-Control on /Employee/Create while signed in",
     "no-store/no-cache so back-button cannot resurface private data", sec_no_cache_authenticated),
    ("SEC12", "Transport", "Baseline browser security headers",
     "Inspect response headers on /Employee",
     "X-Content-Type-Options, frame/CSP, HSTS and Referrer-Policy present", sec_headers),
    ("SEC13", "Transport", "No server/framework version disclosure",
     "Inspect Server / X-Powered-By / X-AspNet-Version headers",
     "The stack and its versions are not advertised", sec_tech_disclosure),
    ("SEC14", "Transport", "Plain HTTP is redirected to HTTPS",
     "Request the site over http://", "Redirected to the https:// origin", sec_https_redirect),
    ("SEC15", "CSRF", "Anti-forgery token is present and enforced",
     "Check the create form for a token, then POST without one",
     "Token present; token-less POST is rejected and stores nothing", sec_antiforgery),
    ("SEC16", "Auth", "Login does not disclose whether a username exists",
     "Compare responses for an unknown user vs a wrong password",
     "Both produce the same generic message", sec_user_enumeration),
    ("SEC17", "Auth", "Repeated failed logins are throttled or locked out",
     "Submit six wrong passwords for admin, then the correct one",
     "Lockout, throttling or CAPTCHA engages", sec_lockout),
    ("SEC18", "Auth", "Registration enforces a password policy",
     "Register with the password '1'", "The weak password is rejected", sec_weak_password),
    ("SEC19", "Auth", "Usernames are unique",
     "Register the same username twice", "The second attempt is rejected", sec_duplicate_user),
]


# ---------------------------------------------------------------- injection & XSS
SQLI = "' OR '1'='1"
XSS = "<script>window.__xss=1</script><img src=x onerror=window.__xss=2>"


def inj_sql_search(c: Any) -> None:
    c.go("/Employee")
    baseline = len(c.page.query_selector_all("table tbody tr"))
    c.go(f"/Employee?searchTerm={SQLI.replace(' ', '+').replace(chr(39), '%27')}&emailTerm=&gradeFilter=")
    body = c.text().lower()
    rows = len(c.page.query_selector_all("table tbody tr"))
    dberr = any(k in body for k in ("sqlexception", "sql syntax", "unclosed quotation",
                                    "sqlite", "ora-", "syntax error near"))
    if dberr:
        c.shot("sqli-error")
        c.res.severity = "High"
        c.fail(f"a SQL injection payload in the search box produced a database error page "
               f"— the input reaches the query engine unparameterised", "High")
    if rows > 0 and "no employees found" not in body and rows >= baseline:
        c.shot("sqli-rows")
        c.res.severity = "High"
        c.fail(f"the tautology payload {SQLI!r} returned {rows} rows (a normal page holds "
               f"{baseline}) instead of an empty result — the filter may be string-concatenated",
               "High")
    c.res.actual = (f"SQL tautology treated as literal text ({rows} rows, no DB error) — "
                    "parameterised query")


def inj_sql_quote(c: Any) -> None:
    for payload in ("O'Brien", "%", "_", "1;DROP TABLE Employee--"):
        c.go(f"/Employee?searchTerm={payload.replace(' ', '+').replace(chr(39), '%27')}"
             "&emailTerm=&gradeFilter=")
        body = c.text().lower()
        if any(k in body for k in ("sqlexception", "unclosed quotation", "syntax error",
                                   "stack trace", "an error occurred while processing")):
            c.shot(f"sql-error-{re.sub('[^a-z0-9]+', '-', payload.lower())[:20]}")
            c.res.severity = "High"
            c.fail(f"search payload {payload!r} produced a server error / SQL exception page", "High")
    c.res.actual = "quote, wildcard and statement-terminator payloads all handled as literals"


def inj_reflected_xss(c: Any) -> None:
    c.page.goto(f"{c.base}/Employee?searchTerm=%3Cscript%3Ewindow.__xss%3D1%3C%2Fscript%3E"
                "&emailTerm=&gradeFilter=", timeout=45000, wait_until="domcontentloaded")
    time.sleep(0.8)
    executed = c.page.evaluate("() => window.__xss || 0")
    html = c.page.content()
    raw = "<script>window.__xss=1</script>" in html
    if executed or raw:
        c.shot("reflected-xss")
        c.res.severity = "Critical"
        c.fail("a <script> payload placed in the searchTerm query parameter is reflected into "
               f"the page unescaped (executed={bool(executed)}) — reflected cross-site scripting",
               "Critical")
    c.res.actual = "the search term is HTML-encoded when echoed back; no script execution"


def inj_stored_xss(c: Any) -> None:
    name = f"QA XSS {_stamp()} {XSS}"
    created = None
    try:
        c.ensure_admin()
        c.go("/Employee/Create")
        c.page.fill("#Name", name)
        c.page.fill("#Age", "30")
        c.page.fill("#Salary", "1000")
        c.page.fill("#DurationWorked", "2")
        c.set_select("#Grade", "Junior")
        c.page.fill("#Email", f"xss{_stamp()}@example.com")
        c.submit("/Employee/Create")
        time.sleep(1.5)
        created = name
        c.page.goto(f"{c.base}/Employee", timeout=45000, wait_until="domcontentloaded")
        time.sleep(1.0)
        executed = c.page.evaluate("() => window.__xss || 0")
        html = c.page.content()
        if executed or "<script>window.__xss=1</script>" in html:
            c.shot("stored-xss")
            c.res.severity = "Critical"
            c.fail("a script payload saved in the employee Name field executes when the list "
                   f"is rendered (window.__xss={executed}) — stored cross-site scripting", "Critical")
        c.res.actual = "script payload stored as text and HTML-encoded on render; no execution"
    finally:
        if created:
            _delete_by_name(c, created)
            for leftover in (created, "QA XSS"):
                if c.find_row(leftover):
                    _delete_by_name(c, leftover)


def inj_bad_id(c: Any) -> None:
    probes = ["/EmployeeDetails/Index/abc", "/EmployeeDetails/Index/-1",
              "/EmployeeDetails/EmployeePF/0", "/Employee/Edit/abc",
              "/EmployeeDetails/Index/1'", "/Employee/Edit/99999999"]
    bad = []
    for p in probes:
        try:
            code, length = c.status_of(p)
        except Exception as e:
            bad.append(f"{p} -> transport error {type(e).__name__}")
            continue
        if code >= 500:
            bad.append(f"{p} -> HTTP {code}")
    if bad:
        c.res.severity = "Medium"
        c.fail("malformed or out-of-range identifiers crash the server instead of returning "
               "a handled 400/404: " + "; ".join(bad), "Medium")
    c.res.actual = f"all {len(probes)} malformed/out-of-range id probes handled without a 5xx"


def inj_oversized_input(c: Any) -> None:
    long_name = "QA Long " + ("A" * 600)
    created = None
    try:
        c.ensure_admin()
        c.go("/Employee/Create")
        c.page.fill("#Name", long_name)
        c.page.fill("#Age", "30")
        c.page.fill("#Salary", "1000")
        c.page.fill("#DurationWorked", "2")
        c.set_select("#Grade", "Junior")
        c.page.fill("#Email", f"long{_stamp()}@example.com")
        c.submit("/Employee/Create")
        time.sleep(1.5)
        body = c.text().lower()
        if "error" in body and ("stack" in body or "exception" in body):
            c.shot("oversized-crash")
            c.res.severity = "Medium"
            c.fail("a 600-character name crashes the save with a server exception page", "Medium")
        row = c.find_row("QA Long ")
        created = row["name"] if row else None
        if row:
            stored = len(row["name"])
            c.res.actual = (f"600-char name accepted and stored ({stored} chars rendered) — "
                            "no server-side maximum length on Name")
            c.note("Name has no maximum-length validation: a 600-character value is stored and "
                   "rendered into the list layout")
        else:
            c.res.actual = "over-long name rejected by validation without a server error"
    finally:
        if created:
            _delete_by_name(c, created)


INJECTION_TESTS = [
    ("INJ01", "Injection", "SQL tautology in the search box",
     "Search for ' OR '1'='1", "Treated as literal text; no DB error, no full dump", inj_sql_search),
    ("INJ02", "Injection", "Quote/wildcard/terminator payloads in search",
     "Search O'Brien, %, _, 1;DROP TABLE Employee--",
     "All handled as literals with no SQL exception page", inj_sql_quote),
    ("INJ03", "XSS", "Reflected XSS via the search parameter",
     "Load /Employee?searchTerm=<script>…</script>",
     "Payload is HTML-encoded and never executes", inj_reflected_xss),
    ("INJ04", "XSS", "Stored XSS via the employee name field",
     "Save an employee whose name contains a script payload, then open the list",
     "Payload is encoded on render and never executes", inj_stored_xss),
    ("INJ05", "Robustness", "Malformed and out-of-range identifiers",
     "Probe six non-numeric/negative/huge ids across detail routes",
     "Handled 400/404 responses, never a 5xx", inj_bad_id),
    ("INJ06", "Validation", "Over-long field input",
     "Save an employee with a 600-character name",
     "Rejected by validation or stored safely, never a server crash", inj_oversized_input),
]


# ---------------------------------------------------------------- input boundaries
def _try_create(c: Any, **fields: Any) -> tuple[bool, str, str]:
    """Attempt a create; returns (accepted, page_text, name)."""
    c.ensure_admin()
    name = fields.pop("name", f"QA Bound {_stamp()}")
    c.go("/Employee/Create")
    c.page.fill("#Name", name)
    c.page.fill("#Age", str(fields.pop("age", 30)))
    c.page.fill("#Salary", str(fields.pop("salary", 1000)))
    c.page.fill("#DurationWorked", str(fields.pop("duration", 5)))
    c.set_select("#Grade", fields.pop("grade", "Junior"))
    c.page.fill("#Email", fields.pop("email", f"b{_stamp()}@example.com"))
    c.submit("/Employee/Create")
    time.sleep(1.4)
    body = c.text()
    return bool(c.find_row(name)), body, name


def val_negative_salary(c: Any) -> None:
    accepted, body, name = _try_create(c, salary=-50000, name=f"QA NegSalary {_stamp()}")
    try:
        if accepted:
            c.shot("negative-salary-accepted")
            c.res.severity = "Medium"
            c.fail("an employee was saved with a salary of -50000 — no range validation on "
                   "Salary, which then feeds the PF and dashboard totals", "Medium")
        c.res.actual = "negative salary rejected by validation"
    finally:
        _delete_by_name(c, name)


def val_age_bounds(c: Any) -> None:
    problems = []
    made = []
    for age, label in ((-5, "negative"), (0, "zero"), (500, "500")):
        accepted, _, name = _try_create(c, age=age, name=f"QA Age{label} {_stamp()}")
        if accepted:
            problems.append(f"{label} ({age})")
            made.append(name)
    try:
        if problems:
            c.shot("age-bounds-accepted")
            c.res.severity = "Medium"
            c.fail("the Age field accepts values outside any plausible range: "
                   + ", ".join(problems) + " — no minimum/maximum validation", "Medium")
        c.res.actual = "negative, zero and 500-year ages are all rejected"
    finally:
        for n in made:
            _delete_by_name(c, n)


def val_nonnumeric_salary(c: Any) -> None:
    """The browser blocks text in <input type=number>, so the real question is whether the
    SERVER rejects it. Post the form directly with a session cookie."""
    c.ensure_admin()
    name = f"QA TextSalary {_stamp()}"
    c.go("/Employee/Create")
    typed = c.page.evaluate("""() => {
        const el = document.querySelector('#Salary');
        return el ? (el.getAttribute('type') || 'text') : 'missing';
    }""")
    r = _api(c, "/Employee/Create", "POST", form={
        "Name": name, "Age": "30", "Salary": "not-a-number", "DurationWorked": "5",
        "Grade": "1", "Email": f"t{_stamp()}@example.com",
    })
    time.sleep(1.2)
    try:
        if c.find_row(name):
            c.shot("text-salary-accepted")
            c.res.severity = "High"
            c.fail(f"the server stored a non-numeric salary ('not-a-number') posted directly to "
                   f"/Employee/Create (HTTP {r.status}) — client-side input type={typed!r} is the "
                   "only thing rejecting it", "High")
        c.res.actual = (f"non-numeric salary rejected server-side (HTTP {r.status}); the field is "
                        f"also client-guarded as input type={typed!r}")
    finally:
        _delete_by_name(c, name)


def val_blank_name(c: Any) -> None:
    accepted, body, name = _try_create(c, name="   ")
    try:
        if accepted or ("required" not in body.lower() and c.find_row("   ")):
            c.shot("whitespace-name-accepted")
            c.res.severity = "Low"
            c.fail("a whitespace-only name passes the required-field check and creates a "
                   "blank row in the employee list", "Low")
        c.res.actual = "whitespace-only name rejected as required"
    finally:
        _delete_by_name(c, name)


def val_unicode_roundtrip(c: Any) -> None:
    name = f"QA Ünïcode 测试 {_stamp()}"
    accepted, _, _ = _try_create(c, name=name)
    try:
        row = c.find_row("QA Ünïcode")
        if not accepted or not row:
            c.shot("unicode-lost")
            c.res.severity = "Medium"
            c.fail(f"an employee named {name!r} could not be saved or retrieved — "
                   "non-ASCII names are not round-tripped", "Medium")
        if row and "测试" not in row["name"]:
            c.shot("unicode-mangled")
            c.res.severity = "Medium"
            c.fail(f"the stored name came back mangled as {row['name']!r} — encoding loss on "
                   "save or render", "Medium")
        c.res.actual = f"unicode name round-trips intact ({row['name']!r})"
    finally:
        _delete_by_name(c, name)


def val_decimal_salary(c: Any) -> None:
    name = f"QA Decimal {_stamp()}"
    accepted, body, _ = _try_create(c, name=name, salary="1234.56")
    try:
        row = c.find_row(name)
        if accepted and row:
            digits = re.sub(r"[^\d.]", "", row["salary"])
            if not digits.startswith("1234.5") and not digits.startswith("1235"):
                c.shot("decimal-salary-mangled")
                c.res.severity = "Medium"
                c.fail(f"salary 1234.56 was stored/rendered as {row['salary']!r} — decimal "
                       "precision is lost", "Medium")
            c.res.actual = f"decimal salary preserved as {row['salary']}"
        else:
            c.res.actual = "decimal salary rejected by validation (integers only)"
            c.note("the Salary field refuses decimal values such as 1234.56")
    finally:
        _delete_by_name(c, name)


def val_duplicate_email(c: Any) -> None:
    email = f"dupe{_stamp()}@example.com"
    a = f"QA Dup A {_stamp()}"
    b = f"QA Dup B {_stamp()}"
    made = []
    try:
        ok_a, _, _ = _try_create(c, name=a, email=email)
        made.append(a)
        ok_b, body, _ = _try_create(c, name=b, email=email)
        if ok_b:
            made.append(b)
            c.res.actual = "two employees can share one email address"
            c.note("duplicate employee emails are allowed — acceptable only if email is not "
                   "treated as an identifier anywhere downstream")
        else:
            c.res.actual = "a duplicate employee email is rejected"
    finally:
        for n in made:
            _delete_by_name(c, n)


BOUNDARY_TESTS = [
    ("VAL01", "Validation", "Negative salary is rejected",
     "Save an employee with salary -50000", "Range validation refuses the value", val_negative_salary),
    ("VAL02", "Validation", "Age accepts only a plausible range",
     "Save employees aged -5, 0 and 500", "All three are rejected", val_age_bounds),
    ("VAL03", "Validation", "Non-numeric salary is rejected",
     "Save an employee with salary 'not-a-number'", "Type validation refuses the value",
     val_nonnumeric_salary),
    ("VAL04", "Validation", "Whitespace-only name is rejected",
     "Save an employee named '   '", "Required-field validation refuses it", val_blank_name),
    ("VAL05", "Data integrity", "Unicode names round-trip",
     "Save and re-read a name with accents and CJK characters",
     "The stored value is byte-identical on render", val_unicode_roundtrip),
    ("VAL06", "Data integrity", "Decimal salary precision",
     "Save salary 1234.56 and re-read it", "Value is preserved or cleanly rejected",
     val_decimal_salary),
    ("VAL07", "Business rule", "Duplicate employee email",
     "Create two employees with the same email address",
     "Either rejected, or allowed by design with no downstream ambiguity", val_duplicate_email),
]


# ---------------------------------------------------------------- business logic
def biz_all_grades(c: Any) -> None:
    bad = []
    for grade in ("Junior", "Middle", "Senior", "C-Level"):
        c.go("/Employee")
        c.set_select("select[name=gradeFilter]", grade)
        c.submit("/Employee")
        time.sleep(0.7)
        wrong = sorted({g for g in _grades(c) if g and g.lower() != grade.lower()})
        kept = c.page.eval_on_selector("select[name=gradeFilter]",
                                       "e => e.options[e.selectedIndex].text") or ""
        if wrong:
            bad.append(f"{grade} -> also returned {wrong}")
        elif kept.strip().lower() != grade.lower():
            bad.append(f"{grade} -> dropdown reset to {kept.strip()!r}")
    if bad:
        c.shot("grade-filter-matrix")
        c.res.severity = "High"
        c.fail("grade filtering is wrong for: " + "; ".join(bad), "High")
    c.res.actual = "all four grades filter correctly and the dropdown keeps the selection"


def biz_email_filter(c: Any) -> None:
    c.go("/Employee")
    rows = c.page.query_selector_all("table tbody tr")
    if not rows:
        c.fail("no employees to derive an email filter from")
    cells = [x.inner_text().strip() for x in rows[0].query_selector_all("td")]
    email = next((v for v in cells if "@" in v), "")
    if not email:
        c.res.status = "skipped"
        c.res.actual = "the list does not render an email column"
        return
    frag = email.split("@")[0][:6]
    c.go(f"/Employee?searchTerm=&emailTerm={frag}&gradeFilter=")
    emails = [c2.inner_text().strip()
              for r in c.page.query_selector_all("table tbody tr")
              for c2 in r.query_selector_all("td") if "@" in c2.inner_text()]
    off = [e for e in emails if frag.lower() not in e.lower()]
    if not emails or off:
        c.shot("email-filter")
        c.res.severity = "Medium"
        c.fail(f"filtering by email fragment {frag!r} returned {len(emails)} rows of which "
               f"{len(off)} do not contain the fragment (e.g. {off[:3]})", "Medium")
    c.res.actual = f"email filter {frag!r} returned {len(emails)} matching rows"


def biz_combined_filter(c: Any) -> None:
    c.go("/Employee")
    c.set_select("select[name=gradeFilter]", "Senior")
    c.submit("/Employee")
    time.sleep(0.7)
    rows = c.page.query_selector_all("table tbody tr")
    if not rows:
        c.res.status = "skipped"
        c.res.actual = "no Senior employees to combine filters on"
        return
    name = rows[0].query_selector_all("td")[0].inner_text().strip()
    token = name.split()[0]
    c.go(f"/Employee?searchTerm={token}&emailTerm=&gradeFilter=3")
    out = [(r.query_selector_all("td")[0].inner_text().strip(),
            r.query_selector_all("td")[4].inner_text().strip())
           for r in c.page.query_selector_all("table tbody tr")
           if len(r.query_selector_all("td")) > 4]
    violations = [x for x in out if token.lower() not in x[0].lower() or x[1].lower() != "senior"]
    if violations:
        c.shot("combined-filter")
        c.res.severity = "Medium"
        c.fail(f"combining searchTerm={token!r} with Grade=Senior returned rows that satisfy "
               f"only one criterion (e.g. {violations[:3]}) — filters are OR-ed, not AND-ed",
               "Medium")
    c.res.actual = f"name + grade filters combine correctly ({len(out)} rows for {token!r} + Senior)"


def biz_dashboard_consistency(c: Any) -> None:
    c.ensure_admin()
    c.go("/Employee")
    listed = c.employee_count()
    c.go("/Home/Dashboard")
    text = c.text()
    nums = [int(n.replace(",", "")) for n in re.findall(r"\b\d{1,3}(?:,\d{3})*\b", text)]
    if listed is None or not nums:
        c.res.status = "skipped"
        c.res.actual = "could not read a comparable total from both screens"
        return
    if not any(abs(n - listed) <= 2 for n in nums):
        c.shot("dashboard-mismatch")
        c.res.severity = "Medium"
        c.fail(f"the employee list reports {listed} employees but no dashboard metric matches "
               f"it (dashboard numbers: {sorted(set(nums))[:8]}) — the two screens disagree",
               "Medium")
    c.res.actual = f"dashboard total agrees with the list count ({listed})"


def biz_edit_isolation(c: Any) -> None:
    """Editing one field must not silently alter the others."""
    name = _admin_create(c, name=f"QA EditIso {_stamp()}", salary=60000, age=41,
                         duration=18, grade="Middle")
    try:
        before = c.find_row(name)
        eid = _id_of(c, name)
        if not before or not eid:
            c.res.status = "skipped"
            c.res.actual = "seed record not found"
            return
        c.go(f"/Employee/Edit/{eid}")
        c.page.fill("#Salary", "61000")
        c.submit("/Employee/Edit")
        time.sleep(1.4)
        after = c.find_row(name)
        if not after:
            c.shot("edit-iso-gone")
            c.fail("the record disappeared after editing one field")
        drift = {k: (before[k], after[k]) for k in ("name", "age", "duration", "grade", "email")
                 if before[k] != after[k]}
        if drift:
            c.shot("edit-side-effects")
            c.res.severity = "High"
            c.fail(f"changing only Salary also changed {list(drift)} ({drift})", "High")
        if "61,000" not in after["salary"]:
            c.shot("edit-not-applied")
            c.res.severity = "High"
            c.fail(f"salary edit did not apply (still {after['salary']})", "High")
        c.res.actual = "editing Salary changed only Salary; all other fields unchanged"
    finally:
        _delete_by_name(c, name)


def biz_delete_cancel(c: Any) -> None:
    name = _admin_create(c, name=f"QA CancelDel {_stamp()}")
    try:
        eid = _id_of(c, name)
        if not eid:
            c.res.status = "skipped"
            c.res.actual = "seed record not found"
            return
        c.go(f"/Employee/Delete/{eid}")
        body = c.text()
        if name not in body:
            c.shot("delete-wrong-record")
            c.res.severity = "High"
            c.fail(f"the delete confirmation page for id {eid} does not show the record it is "
                   f"about to delete ({name!r})", "High")
        back = c.page.query_selector("a[href*='/Employee']")
        if back:
            back.click()
            c.page.wait_for_load_state("domcontentloaded")
            time.sleep(1.0)
        else:
            c.go("/Employee")
        if not c.find_row(name):
            c.shot("delete-on-cancel")
            c.res.severity = "Critical"
            c.fail(f"navigating away from the delete confirmation removed {name!r} anyway — "
                   "the record is deleted without confirming", "Critical")
        c.res.actual = "the confirmation page names the record and cancelling leaves it intact"
    finally:
        _delete_by_name(c, name)


def biz_pf_generalises(c: Any) -> None:
    """PF math must hold for a record whose salary/duration we control."""
    name = _admin_create(c, name=f"QA PFMath {_stamp()}", salary=120000, duration=10,
                         grade="Senior")
    try:
        eid = _id_of(c, name)
        if not eid:
            c.res.status = "skipped"
            c.res.actual = "seed record not found"
            return
        c.go(f"/EmployeeDetails/EmployeePF/{eid}")
        body = c.text()
        nums = re.findall(r"[₹$]\s*([\d,]+(?:\.\d+)?)", body)
        months = re.search(r"(\d+)\s*months", body)
        if not nums or not months:
            c.res.status = "skipped"
            c.res.actual = f"PF page for the seeded record is unreadable ({c.page.url})"
            return
        total = float(nums[0].replace(",", ""))
        salary = float(nums[1].replace(",", "")) if len(nums) > 1 else 0.0
        m = int(months.group(1))
        expected = round(salary * 0.12 * m, 2)
        if abs(total - expected) > 0.05:
            c.shot("pf-generalisation")
            c.res.severity = "High"
            c.fail(f"for a controlled record (salary 120000, 10 months) the PF total {total} "
                   f"does not equal 12% × {salary} × {m} = {expected}", "High")
        c.res.actual = (f"PF formula holds for a second, controlled record "
                        f"(₹{total} = 12% × {salary} × {m})")
    finally:
        _delete_by_name(c, name)


def biz_pagination_integrity(c: Any) -> None:
    seen: dict[str, int] = {}
    pages = 0
    for p in (1, 2, 3):
        c.go(f"/Employee?page={p}")
        # Key rows by their record id (from the row's Edit/Details link), never by name —
        # the shared instance genuinely contains distinct employees with identical names.
        ids = c.page.evaluate("""() => [...document.querySelectorAll('table tbody tr')].map(tr => {
            const a = tr.querySelector("a[href*='/Edit/'], a[href*='EmployeeDetails']");
            const m = a && a.getAttribute('href').match(/\\/(\\d+)\\s*$/);
            return m ? m[1] : (tr.querySelector('td') ? 'name:' + tr.querySelector('td').innerText.trim() : null);
        }).filter(Boolean)""")
        if not ids:
            break
        pages += 1
        for n in ids:
            seen[n] = seen.get(n, 0) + 1
    dupes = [n for n, k in seen.items() if k > 1]
    showing = re.search(r"Showing\s+([\d,]+)\s*[–-]\s*([\d,]+)\s+of\s+([\d,]+)", c.text())
    if pages < 2:
        c.res.status = "skipped"
        c.res.actual = "fewer than two pages of data"
        return
    if dupes:
        c.shot("pagination-duplicates")
        c.res.severity = "Medium"
        c.fail(f"{len(dupes)} employee(s) appear on more than one page of the same listing "
               f"(e.g. {dupes[:3]}) — the page window overlaps", "Medium")
    if showing:
        lo, hi, tot = (int(g.replace(",", "")) for g in showing.groups())
        if hi < lo or hi > tot:
            c.shot("showing-counter")
            c.res.severity = "Low"
            c.fail(f"the 'Showing {lo}–{hi} of {tot}' counter is inconsistent", "Low")
    c.res.actual = (f"{pages} pages walked, {len(seen)} distinct rows, no overlap"
                    + (f"; counter '{showing.group(0)}' consistent" if showing else ""))


BUSINESS_TESTS = [
    ("BIZ01", "Filter", "Grade filter is correct for all four grades",
     "Filter the list by Junior, Middle, Senior and C-Level in turn",
     "Each filter returns only that grade and keeps the selection", biz_all_grades),
    ("BIZ02", "Filter", "Email filter matches on the email column",
     "Filter by a fragment of an existing email", "Only rows containing the fragment come back",
     biz_email_filter),
    ("BIZ03", "Filter", "Name and grade filters combine (AND)",
     "Search a name token with Grade=Senior",
     "Only rows satisfying both criteria are returned", biz_combined_filter),
    ("BIZ04", "Data integrity", "Dashboard total agrees with the employee list",
     "Compare the list's record count with the dashboard metrics",
     "The two screens report the same population", biz_dashboard_consistency),
    ("BIZ05", "Edit", "Editing one field leaves the others untouched",
     "Change only Salary on a seeded record and re-read every field",
     "Only Salary changes", biz_edit_isolation),
    ("BIZ06", "Delete", "Delete confirmation is accurate and cancellable",
     "Open Delete for a seeded record, verify it names the record, then navigate away",
     "The record is named and survives a cancel", biz_delete_cancel),
    ("BIZ07", "PF", "PF formula holds for a controlled record",
     "Seed salary 120000 / 10 months and open its PF page",
     "Total equals 12% × monthly salary × months", biz_pf_generalises),
    ("BIZ08", "Pagination", "Pages do not overlap and the counter is consistent",
     "Walk pages 1–3 and compare the row sets and the 'Showing X–Y of Z' counter",
     "No row appears twice; the counter is coherent", biz_pagination_integrity),
]


# ---------------------------------------------------------------- ops, a11y, UX
def ops_response_times(c: Any) -> None:
    slow = []
    timings = {}
    for p in KEY_PAGES:
        t0 = time.time()
        try:
            _api(c, p)
        except Exception as e:
            slow.append(f"{p} (transport error {type(e).__name__})")
            continue
        dt = round(time.time() - t0, 2)
        timings[p] = dt
        if dt > 3.0:
            slow.append(f"{p} {dt}s")
    XSTATE["timings"] = timings
    if slow:
        c.res.severity = "Low"
        c.fail("pages slower than the 3s budget: " + ", ".join(slow)
               + f" (all timings: {timings})", "Low")
    c.res.actual = "all key pages responded within 3s: " + ", ".join(
        f"{k} {v}s" for k, v in timings.items())


def ops_broken_links(c: Any) -> None:
    c.ensure_admin()
    seen: set[str] = set()
    for p in ("/", "/Employee", "/Home/Dashboard"):
        c.go(p)
        for a in c.page.query_selector_all("a[href]"):
            href = (a.get_attribute("href") or "").strip()
            if (not href or href.startswith(("#", "mailto:", "javascript:", "tel:"))
                    or href.startswith("http") and c.base not in href):
                continue
            if "/Delete/" in href or "logoff" in href.lower():
                continue
            seen.add(href if href.startswith("http") else href)
    broken = []
    for href in sorted(seen)[:40]:
        try:
            code, _ = c.status_of(href)
        except Exception as e:
            broken.append(f"{href} ({type(e).__name__})")
            continue
        if code >= 400:
            broken.append(f"{href} -> {code}")
    if broken:
        c.res.severity = "Medium"
        c.fail(f"{len(broken)} of {len(seen)} internal links are broken: "
               + "; ".join(broken[:8]), "Medium")
    c.res.actual = f"all {len(seen)} internal links on the main screens resolve (<400)"


def ops_static_caching(c: Any) -> None:
    c.go("/")
    assets = []
    for sel, attr in (("link[rel=stylesheet]", "href"), ("script[src]", "src"),
                      ("img[src]", "src")):
        for el in c.page.query_selector_all(sel):
            v = el.get_attribute(attr) or ""
            if v and not v.startswith("data:") and (v.startswith("/") or c.base in v):
                assets.append(v)
    if not assets:
        c.res.status = "skipped"
        c.res.actual = "the page references no local static assets"
        return
    uncached = []
    for a in assets[:10]:
        h = _headers(c, a)
        if "max-age" not in (h.get("cache-control") or "") and "etag" not in h and \
                "last-modified" not in h:
            uncached.append(a)
    if uncached:
        c.res.severity = "Low"
        c.fail(f"{len(uncached)} static assets are served with no caching or validation "
               f"headers (e.g. {uncached[:3]}) — every page view refetches them", "Low")
    c.res.actual = f"static assets carry cache/validation headers ({len(assets)} checked)"


def ops_compression(c: Any) -> None:
    r = _api(c, "/Employee", headers={"Accept-Encoding": "gzip, deflate, br"})
    h = {k.lower(): v for k, v in r.headers.items()}
    enc = h.get("content-encoding", "")
    size = len(r.body())
    if not enc:
        c.res.severity = "Low"
        c.fail(f"the employee list is served uncompressed ({size} bytes, no Content-Encoding) "
               "even though the client advertised gzip/br", "Low")
    c.res.actual = f"responses are compressed ({enc}, {size} bytes on the wire)"


def ops_verb_handling(c: Any) -> None:
    probes = []
    for path, method in (("/Employee", "POST"), ("/Employee/Create", "DELETE"),
                         ("/Account/Login", "PUT")):
        try:
            r = _api(c, path, method)
            probes.append((path, method, r.status))
        except Exception as e:
            probes.append((path, method, f"err:{type(e).__name__}"))
    bad = [f"{m} {p} -> {s}" for p, m, s in probes if isinstance(s, int) and s >= 500]
    if bad:
        c.res.severity = "Medium"
        c.fail("unexpected HTTP verbs crash the server instead of returning 404/405: "
               + "; ".join(bad), "Medium")
    c.res.actual = "unexpected verbs handled without a 5xx: " + ", ".join(
        f"{m} {p}={s}" for p, m, s in probes)


def ops_robots(c: Any) -> None:
    findings = []
    for p in ("/robots.txt", "/sitemap.xml"):
        code, length = c.status_of(p)
        findings.append(f"{p} -> {code} ({length} bytes)")
    if all("404" in f for f in findings):
        c.note("neither robots.txt nor sitemap.xml is served — acceptable for an internal "
               "app, but crawlers get no guidance for the public pages")
    c.res.actual = "; ".join(findings)


def ux_console_errors(c: Any) -> None:
    problems: list[str] = []
    c.page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text[:120]}")
              if m.type == "error" else None)
    c.page.on("pageerror", lambda e: problems.append(f"pageerror: {str(e)[:120]}"))
    for p in ("/", "/Employee", "/Home/Dashboard"):
        c.go(p)
        time.sleep(1.0)
    real = [p for p in problems if "favicon" not in p.lower()]
    if real:
        c.shot("console-errors")
        c.res.severity = "Low"
        c.fail(f"{len(real)} JavaScript/console errors on the main screens: {real[:4]}", "Low")
    c.res.actual = "no JavaScript errors on the home, list and dashboard screens"


def ux_search_state(c: Any) -> None:
    c.go("/Employee")
    rows = c.page.query_selector_all("table tbody tr")
    if not rows:
        c.fail("no employees to search with")
    term = rows[0].query_selector_all("td")[0].inner_text().strip().split()[0]
    c.page.fill("input[name=searchTerm]", term)
    c.submit("/Employee")
    time.sleep(0.8)
    kept = c.page.eval_on_selector("input[name=searchTerm]", "e => e.value") or ""
    if kept.strip().lower() != term.lower():
        c.shot("search-term-lost")
        c.res.severity = "Low"
        c.fail(f"after searching for {term!r} the search box shows {kept!r} — the user cannot "
               "see or refine what they searched for", "Low")
    c.res.actual = f"the search box retains {kept!r} after submitting"


def ux_tablet_layout(c: Any) -> None:
    ctx2 = c.page.context.browser.new_context(viewport={"width": 768, "height": 1024})
    p2 = ctx2.new_page()
    try:
        p2.goto(f"{c.base}/Employee", timeout=45000, wait_until="domcontentloaded")
        time.sleep(0.8)
        m = p2.evaluate("() => ({doc: document.documentElement.scrollWidth,"
                        " win: window.innerWidth})")
        rel = f"screenshots/{c.res.id}-tablet-768px.png"
        (c.out / rel).parent.mkdir(parents=True, exist_ok=True)
        p2.screenshot(path=str(c.out / rel), full_page=True)
        c.res.screenshots.append(rel)
        if m["doc"] - m["win"] > 20:
            c.res.severity = "Low"
            c.fail(f"at 768px the document is {m['doc']}px wide vs a {m['win']}px viewport "
                   f"({m['doc'] - m['win']}px horizontal overflow)", "Low")
        c.res.actual = f"no horizontal overflow at 768px (doc {m['doc']}px / win {m['win']}px)"
    finally:
        ctx2.close()


def a11y_document(c: Any) -> None:
    problems = []
    titles = {}
    for p in KEY_PAGES:
        c.go(p)
        lang = c.page.get_attribute("html", "lang") or ""
        title = (c.page.title() or "").strip()
        h1 = len(c.page.query_selector_all("h1"))
        titles[p] = title
        if not lang:
            problems.append(f"{p}: <html> has no lang attribute")
        if not title:
            problems.append(f"{p}: empty <title>")
        if h1 == 0:
            problems.append(f"{p}: no <h1> heading")
    dupes = [t for t in set(titles.values()) if t and list(titles.values()).count(t) > 1]
    if dupes:
        problems.append(f"duplicate page titles across screens: {dupes}")
    if problems:
        c.res.severity = "Low"
        c.fail("document-level accessibility gaps: " + "; ".join(problems[:6]), "Low")
    c.res.actual = f"every key page declares lang, a unique title and an h1 ({len(titles)} pages)"


def a11y_forms_images(c: Any) -> None:
    problems = []
    c.ensure_admin()
    for p in ("/Employee/Create", "/Account/Login", "/Account/Register"):
        c.go(p)
        unlabelled = c.page.evaluate("""() => {
            const out = [];
            for (const el of document.querySelectorAll('input:not([type=hidden]):not([type=submit]), select, textarea')) {
                const id = el.getAttribute('id');
                const hasLabel = (id && document.querySelector(`label[for="${id}"]`))
                    || el.closest('label')
                    || el.getAttribute('aria-label')
                    || el.getAttribute('aria-labelledby');
                if (!hasLabel) out.push(el.getAttribute('name') || el.getAttribute('id') || el.tagName);
            }
            return out;
        }""")
        if unlabelled:
            problems.append(f"{p}: unlabelled controls {unlabelled[:5]}")
    c.go("/")
    noalt = c.page.evaluate(
        "() => [...document.querySelectorAll('img')].filter(i => !i.hasAttribute('alt')).length")
    if noalt:
        problems.append(f"home page: {noalt} <img> without alt text")
    if problems:
        c.res.severity = "Low"
        c.fail("form/image accessibility gaps: " + "; ".join(problems), "Low")
    c.res.actual = "all form controls are labelled and images carry alt text"


def clean_leftovers(c: Any) -> None:
    """Every seeded record must be gone by the end of the run."""
    c.ensure_admin()
    remaining = []
    for token in ("QA Regression", "QA Temp", "QA Bound", "QA PrivEsc", "QA DelProbe",
                  "QA CSRF", "QA AnonPost", "QA XSS", "QA Long", "QA NegSalary",
                  "QA Age", "QA TextSalary", "QA Ünïcode", "QA Decimal", "QA Dup",
                  "QA EditIso", "QA CancelDel", "QA PFMath"):
        for _ in range(4):
            row = c.find_row(token)
            if not row:
                break
            if not _delete_by_name(c, row["name"]):
                remaining.append(row["name"])
                break
    XSTATE["leftovers"] = remaining
    if remaining:
        c.res.severity = "Low"
        c.fail(f"{len(remaining)} seeded test record(s) could not be removed: {remaining[:5]}",
               "Low")
    c.res.actual = "no seeded test records left in the database"
    if XSTATE.get("user"):
        c.note(f"the registered test account {XSTATE['user']!r} remains — the app offers no "
               "self-service account deletion")


OPS_TESTS = [
    ("OPS01", "Performance", "Key pages respond within 3s",
     "Time the home, list, login, register and details screens",
     "Every page is under the 3s budget", ops_response_times),
    ("OPS02", "Links", "No broken internal links",
     "Collect and request every internal link on the main screens",
     "All resolve with a status below 400", ops_broken_links),
    ("OPS03", "Performance", "Static assets are cacheable",
     "Inspect cache headers on CSS/JS/image assets",
     "max-age, ETag or Last-Modified present", ops_static_caching),
    ("OPS04", "Performance", "Responses are compressed",
     "Request /Employee with Accept-Encoding: gzip, br",
     "Content-Encoding is applied", ops_compression),
    ("OPS05", "Robustness", "Unexpected HTTP verbs are handled",
     "POST /Employee, DELETE /Employee/Create, PUT /Account/Login",
     "404/405 rather than a server crash", ops_verb_handling),
    ("OPS06", "SEO", "robots.txt and sitemap.xml",
     "Request both files", "Served, or absent by design", ops_robots),
    ("UX01", "UI", "No JavaScript errors on the main screens",
     "Watch the console on home, list and dashboard",
     "No console errors or unhandled exceptions", ux_console_errors),
    ("UX02", "UI", "Search box retains the submitted term",
     "Search a name and re-read the input value",
     "The term stays in the box for refinement", ux_search_state),
    ("UX03", "Responsive", "Employee list at a 768px tablet viewport",
     "Open /Employee at 768×1024", "No horizontal overflow", ux_tablet_layout),
    ("A11Y01", "Accessibility", "Document language, title and heading structure",
     "Inspect html[lang], <title> and <h1> across the key pages",
     "Each page declares a language, a unique title and one h1", a11y_document),
    ("A11Y02", "Accessibility", "Form controls are labelled and images have alt text",
     "Inspect the create/login/register forms and home page images",
     "Every control has a label; every image has alt text", a11y_forms_images),
    ("CLEAN", "Hygiene", "No test data left behind",
     "Search for every seeded prefix and delete what remains",
     "The database is left exactly as found", clean_leftovers),
]

EXTRA_TESTS = RBAC_TESTS + SESSION_TESTS + INJECTION_TESTS + BOUNDARY_TESTS \
    + BUSINESS_TESTS + OPS_TESTS
