# EA Employee App — Test Results

Exploratory / functional test run against **https://eaapp.somee.com** (ExecuteAutomation Employee App, ASP.NET Core 8 + EF Core + Identity).

| | |
|---|---|
| Application under test | https://eaapp.somee.com |
| Test date | 5–6 Aug 2026 (Pacific/Auckland) |
| Environment | Cloud browser (Chromium), desktop viewport |
| Account used | `admin` / `password` (Administrator role) |
| Tests executed | 12 |
| Passed | 10 |
| Failed | 2 |
| Test data | Created and removed `QA TestUser 20260805` (employee id 508) |

## Summary

Core navigation, authentication, employee listing, name search and the Create / Delete paths all behave correctly. Two functional defects were found: the **Grade filter does not filter**, and **editing an employee does not persist any change**. Both are reproducible and silent — no error is shown to the user.

## Test results

| # | Area | Test | Steps | Expected | Actual | Result |
|---|------|------|-------|----------|--------|--------|
| 1 | Navigation | Home page loads | Open `/` | Landing page with nav (Home, Employees, Dashboard, About) and Register / Login | Renders as expected | ✅ Pass |
| 2 | Employee list | List loads with paging | Open `/Employee` | Table of employees with pagination | 278 records, 56 pages, "Showing 1–5 of 278" | ✅ Pass |
| 3 | Search | Search by exact name | Search `BOyd` | Matching row returned | `BOyd` row returned | ✅ Pass |
| 4 | Search | Case-insensitive search | Search `boyd` | Matches `BOyd` | `BOyd` row returned | ✅ Pass |
| 5 | Search | No-match search | Search `ZZZ_NO_MATCH_ZZZ` | Empty-state message | "👥 No employees found." | ✅ Pass |
| 6 | Auth | Login with empty credentials | Submit login with both fields blank | Required-field validation | "The User Name field is required." / "The Password field is required." | ✅ Pass |
| 7 | Auth | Login with wrong password | `admin` / `wrongpass123` | Rejected with error | "Invalid login attempt." | ✅ Pass |
| 8 | Auth | Login with valid credentials | `admin` / `password` | Authenticated, admin actions visible | "Hello admin!" + `+ New Employee`, `Edit`, `Delete` appear | ✅ Pass |
| 9 | Create | Client-side validation on empty create | Submit `/Employee/Create` empty | All fields flagged required | All 5 required messages shown | ✅ Pass |
| 10 | Create | Email format validation | Enter `not-an-email`, submit | Email format error | "The Email field is not a valid e-mail address." | ✅ Pass |
| 11 | Create | Create a valid employee | Name `QA TestUser 20260805`, Age 30, Salary 75000, Duration 24, Grade Senior, Email `qa.test.20260805@example.com` | Saved, redirect to list, record searchable | Redirected to list, count 277 → 278, record found with all values correct | ✅ Pass |
| 12 | Delete | Delete an employee | `Delete` on id 508 → confirm | Record removed | Redirected to list; record no longer searchable, `/Employee/Details/508` returns an error page | ✅ Pass |
| 13 | Filter | Filter by Grade | Select Grade `Junior`, Search | Only Junior employees listed | Senior, Middle and C-Level rows returned; dropdown resets to "All Grades" | ❌ **Fail** |
| 14 | Edit | Update an existing employee | Edit id 508: Salary 75000 → 82000, Grade Senior → C-Level, Save | Changes persisted | List and reopened edit form still show 75000 / Senior; no error shown | ❌ **Fail** |

## Defects

### BUG-01 — Grade filter returns unfiltered results (High)

**Steps to reproduce**
1. Open `/Employee`.
2. Select `Junior` in the **Grade** dropdown and click **Search**
   (equivalently: `GET /Employee?searchTerm=&emailTerm=&gradeFilter=Junior`).

**Expected:** only employees with grade Junior are listed, and the dropdown keeps `Junior` selected.

**Actual:** the result set includes Senior, Middle and C-Level employees — the same rows as an unfiltered list. The dropdown also resets to "All Grades", so the applied filter is not reflected back to the user.

**Impact:** the grade filter is unusable; users cannot narrow the 278-record list by grade and get silently wrong results with no indication that the filter was ignored.

**Note:** the name and email search parameters on the same form work correctly, which points at the grade filter binding/predicate specifically rather than the search form as a whole.

### BUG-02 — Editing an employee does not persist changes (High)

**Steps to reproduce**
1. Log in as `admin`.
2. Open an employee's **Edit** page (e.g. `/Employee/Edit/508`).
3. Change **Monthly Salary** from `75000` to `82000` and **Grade** from `Senior` to `C-Level`.
4. Click **✓ Save Changes**.

**Expected:** changes are saved and reflected in the list and on the edit form.

**Actual:** the form posts and the page returns without any validation or error message, but the values are unchanged. The employee list still shows `$75,000.00` / `Senior`, and reopening the edit form shows the original values. Reproduced twice (submit button and Enter-key submit).

**Impact:** no employee record can be updated. Data corrections are impossible, and because the app reports no error, users will believe their edit succeeded.

## Minor observations

- **Form state lost after a failed create.** When a create submission fails validation, the re-rendered form keeps Name, Age, Salary and Email but clears **Duration Worked** and **Grade**, forcing the user to re-enter them.
- **Empty page title.** Every page is served with an empty `<title>`, which hurts browser tabs, bookmarks, history and SEO.
- **Shared test data.** The employee count moved (278 → 280) between steps independently of this run, so the instance appears to be shared/publicly writable. Absolute record counts are not a reliable assertion target for automated tests here.

## Coverage notes

Not covered in this run: user registration, the Dashboard and PF/contribution calculations, the Details view content, role-based access as a non-admin user, pagination boundary behaviour beyond page 1, and responsive/mobile layout.
