# EA Employee App — Automated Test Results

Automated functional regression against **https://eaapp.somee.com** (ExecuteAutomation Employee App — ASP.NET Core 8 + EF Core + Identity).

| | |
|---|---|
| Application under test | https://eaapp.somee.com |
| Run started | 06 Aug 2026, 06:23 NZST |
| Duration | 88s |
| Runner | Playwright + Chromium (headless, 1440×900), one isolated browser context per test |
| Tests | **25** |
| Passed | **23** |
| Failed | **2** |
| Errors | 0 |
| Skipped | 0 |
| Evidence | full-page screenshot at each failure + screen recording of each failing workflow |

## Failures at a glance

| # | Severity | Test | What happened | Evidence |
|---|---|---|---|---|
| TC19b | Medium | Company contribution matches its printed formula | company contribution ₹278.46 contradicts the formula printed on the same page (PF 15% + 3×2% = 21% × 51.0 × 30 months = 321.3); the value implies an effective rate of 18.2% | [video](videos/TC19b-small.mp4) · [shot 1](screenshots/TC19b-bonus-formula-mismatch.jpg) |
| TC19c | Low | Salary currency symbol is consistent across screens | salary is rendered as $ on the employee list but $/₹ on the details/PF screens — same field, two currencies | [video](videos/TC19c-small.mp4) · [shot 1](screenshots/TC19c-currency-list.jpg) |

## Corrections to the previous (manual) run

Two defects reported in the earlier manual pass do **not** reproduce under this automated suite. Both were driving errors on my side, not application faults — recording them here so the earlier report is not taken at face value:

| Previously reported | Verdict now | Why the manual run was wrong |
|---|---|---|
| "Grade filter returns unfiltered results" | **Not a defect** (TC06 passes for all four grades) | The filter binds the option **value** (`1`–`4`), not the label. The manual test requested `?gradeFilter=Junior`, which the app cannot bind, so it fell back to an unfiltered list. Submitting the real form produces `?gradeFilter=1` and returns only Junior rows (69 of them), and the dropdown keeps the selection. |
| "Editing an employee never persists" | **Not a defect** (TC16 passes: salary 75,000 → 82,000 and grade Senior → C-Level both persist) | The manual run set the field values by script injection and submitted the page's first submit button. The layout renders the header **Logout** form before the edit form, so that click logged the session out instead of saving. Typing into the fields and submitting the edit form itself saves correctly. |

The lesson is baked into the harness: every submit is scoped to its own form (`Ctx.submit("/Employee/Edit")`), never to the first `button[type=submit]` on the page.

## Full results

| # | Area | Test | Steps | Expected | Actual | Result |
|---|------|------|-------|----------|--------|--------|
| TC01 | Navigation | Home page loads with primary navigation | Open / | Landing page renders with Home / Employees / About navigation | Home page rendered; title='Home - EAEmployee'; nav present | ✅ Pass |
| TC02 | Employee list | Employee list renders with record count | Open /Employee | Table of employees plus a total count | 280 employees reported, 5 rows on page 1 | ✅ Pass |
| TC03 | Search | Search by an existing employee name | Search the first listed name | That employee's row is returned | search 'BOyd' -> row returned (gruity@gmail.com) | ✅ Pass |
| TC04 | Search | Search is case-insensitive | Search the same name with flipped case | The same row is returned | 'boYD' matched 'BOyd' — search is case-insensitive | ✅ Pass |
| TC05 | Search | No-match search shows an empty state | Search ZZZ_NO_MATCH_ZZZ | 'No employees found.' message | empty state shown: 'No employees found.' | ✅ Pass |
| TC06 | Filter | Filter the list by Grade | Select Grade=Junior and submit the search form | Only Junior employees are listed and the dropdown keeps the selection | only Junior rows returned (5 rows) | ✅ Pass |
| TC07 | Auth | Login with empty credentials is rejected | Submit /Account/Login with both fields blank | Required-field validation on both fields | both fields flagged required | ✅ Pass |
| TC08 | Auth | Login with a wrong password is rejected | Submit admin / wrongpass123 | 'Invalid login attempt.' | 'Invalid login attempt.' shown | ✅ Pass |
| TC09 | Auth | Login with valid admin credentials | Submit admin / password | Authenticated; admin-only actions become visible | authenticated as admin; New Employee / Edit / Delete visible | ✅ Pass |
| TC10 | Auth | Anonymous user cannot reach admin screens | Open /Employee and /Employee/Create in a clean session | No admin actions on the list; /Employee/Create redirects to login | anonymous user sees read-only list; /Employee/Create redirects to login | ✅ Pass |
| TC11 | Auth | Register a new user account | Submit /Account/Register with a unique username, email and valid password | Account is created without error | registered 'qauser20260806062320' and was signed in automatically | ✅ Pass |
| TC12 | Create | Create form validates required fields | Submit /Employee/Create empty | Every required field is flagged | required-field validation fired on empty submit | ✅ Pass |
| TC13 | Create | Create form validates email format | Submit the create form with Email=not-an-email | Email format error is shown | email format rejected: 'The Email field is not a valid e-mail address.' | ✅ Pass |
| TC14 | Create | Create a valid employee record | Fill every field with valid data and submit | Record is saved with the exact values and appears in the list | created 'QA Regression 20260806062335' (id 516), all values stored correctly | ✅ Pass |
| TC15 | Details | Details view shows the record's data | Open Details for the created employee | Name, salary, grade and duration are shown | details page (/EmployeeDetails/Index/516) shows the selected employee | ✅ Pass |
| TC16 | Edit | Update an existing employee | Edit the created employee: salary 75000→82000, grade Senior→C-Level, save | Changes are persisted and visible in the list | edit persisted: salary $82,000.00, grade C-Level | ✅ Pass |
| TC17 | Delete | Delete an employee | Delete the created employee and confirm | Record is removed from the list | 'QA Regression 20260806062335' removed; no longer returned by search | ✅ Pass |
| TC18 | Dashboard | Dashboard renders metrics | Open /Dashboard as admin | Dashboard renders with employee metrics | dashboard rendered with metrics (sample: ['280', '$39,060', '31', '3', '$10,936,834', '5']) | ✅ Pass |
| TC19 | PF | PF contribution total matches its own formula | Open an employee's PF Contribution page | Total equals 12% × monthly salary × months worked | PF total ₹183.6 == 12% × 51.0 × 30 months (employee 79) | ✅ Pass |
| TC19b | PF | Company contribution matches its printed formula | Open the same employee's Company Contribution page | Total equals the PF% + grade-allowance formula shown on the page | company contribution ₹278.46 contradicts the formula printed on the same page (PF 15% + 3×2% = 21% × 51.0 × 30 months = 321.3); the value implies an effective rate of 18.2% | ❌ **Fail** |
| TC19c | Consistency | Salary currency symbol is consistent across screens | Compare the currency symbol on /Employee and /EmployeeDetails | The same salary field uses one currency symbol everywhere | salary is rendered as $ on the employee list but $/₹ on the details/PF screens — same field, two currencies | ❌ **Fail** |
| TC20 | Pagination | Deep pagination returns different rows | From /Employee go to page 3 (or Next) | A distinct set of rows loads | deep page loaded distinct rows (https://eaapp.somee.com/Employee?page=3); Showing 11–15 of 280 | ✅ Pass |
| TC21 | Pagination | Last page and out-of-range page behave sanely | Open the last page, then a page number far beyond it | Last page has rows; out-of-range clamps or empties without an error page | page=56 rendered 5 rows; out-of-range page=556 handled gracefully (1 rows, no error page) | ✅ Pass |
| TC22 | Responsive | Employee list at a 390px mobile viewport | Open /Employee at 390×844 | No horizontal overflow of the document | no horizontal overflow at 390px (doc 390px / win 390px) | ✅ Pass |
| TC23 | Robustness | Non-existent record id is handled | Open /Employee/Details/99999999 | Handled 404/redirect with no framework internals leaked | HTTP 404 with an empty body — correct status, no branded error page | ✅ Pass |

## Observations (not test failures)

- **TC23** — 404 responses have an empty body (no styled 'not found' page), so a browser shows its own network-error screen instead of the app's UI

## Failure detail

### TC19b — Company contribution matches its printed formula  ·  severity Medium

**Steps:** Open the same employee's Company Contribution page

**Expected:** Total equals the PF% + grade-allowance formula shown on the page

**Actual:** company contribution ₹278.46 contradicts the formula printed on the same page (PF 15% + 3×2% = 21% × 51.0 × 30 months = 321.3); the value implies an effective rate of 18.2%

**Recording of the failing workflow:** [`videos/TC19b-small.mp4`](videos/TC19b-small.mp4)

![TC19b failure](screenshots/TC19b-bonus-formula-mismatch.jpg)

### TC19c — Salary currency symbol is consistent across screens  ·  severity Low

**Steps:** Compare the currency symbol on /Employee and /EmployeeDetails

**Expected:** The same salary field uses one currency symbol everywhere

**Actual:** salary is rendered as $ on the employee list but $/₹ on the details/PF screens — same field, two currencies

**Recording of the failing workflow:** [`videos/TC19c-small.mp4`](videos/TC19c-small.mp4)

![TC19c failure](screenshots/TC19c-currency-list.jpg)

## How to re-run

```bash
python3 scripts/ea_regression.py --out output/ea-regression
python3 scripts/render_report.py --results output/ea-regression/results.json
```

`--only TC06,TC16` runs a subset, `--keep-all-videos` records passing tests too, `--base-url` points the suite at another deployment. Test data is created with a timestamped name and deleted by the delete test in the same run.

<sub>Generated 06 Aug 2026, 06:25 NZST from `results.json`.</sub>