# vaporFUND engineering guide

## Architecture

vaporFUND is a Django app for tracking ETF portfolios and planning buy-only purchases from cash contributions. Recommendations are drafts for manual brokerage execution. The app records completed purchases.

- `config/` contains Django settings and URL configuration. `portfolio/models.py` and `portfolio/migrations/` define persisted data.
- Keep allocation calculations in `portfolio/services.py`. Use `portfolio/workflows.py` for contribution, recommendation, and execution writes. Preserve transaction boundaries so cash, executions, lots, and recommendation status stay consistent.
- Route browser requests through `portfolio/urls.py`, `views.py`, and `forms.py`. Management commands live in `portfolio/management/commands/`.

## Account isolation

- Pass the selected account explicitly to services and workflows in multi-account paths. Preserve account context in links, form submissions, and redirects.
- Scope cash, contributions, and targets by account. Scope recommendations through their contribution, executions through their recommendation's contribution, and lots through their execution's recommendation and contribution. Resolve contribution sequence numbers within the account.
- ETF definitions and price history are shared. Target weights and portfolio balances belong to the account. Use the existing active-account selection and keep authorization checks independent of request-supplied account IDs.

## Allocation and precision

- Use the latest closing price on or before the valuation date. The allocation service rejects missing or nonpositive prices. Value open positions using remaining shares, and include the account's cash in portfolio value. Enabled ETFs with positive account targets must total 100%.
- Prioritize the largest dollar shortfall. Use affordable whole shares that strictly reduce absolute deviation, preferring fewer shares when the deviation ties. Carry cash forward when the priority ETF cannot be bought under these rules. Recalculate priority after each simulated buy.
- For execution, preserve the recommended share limit and check account cash against the actual trade total, including fees. Keep purchases manual and retain the existing duplicate-execution protection.
- Use `Decimal` for prices, money, and percentages. Construct decimal literals from strings. Follow the existing `0.01` quantization for money and calculated allocation percentages, retain per-share precision until the total is calculated, and use integers for share quantities.

## Tests

From the repository root, use a Python environment with `requirements.txt` installed. Django tests are in `portfolio/tests.py`.

```sh
python manage.py test portfolio.tests.AllocationServiceTests portfolio.tests.MultiAccountIsolationTests
python manage.py test
```

Run the complete suite before requesting review. Add regression coverage for changed behavior. For account-scoped changes, test with at least two accounts and verify that lookups, calculations, and rendered data use the selected account. For allocation changes, cover affordability, whole-share rounding, and cash carry-forward.

## UI

Use Django templates under `portfolio/templates/portfolio/` and reuse `portfolio/static/portfolio/dashboard.css`. Keep table wrappers and responsive layouts intact. Use named Django URLs, labeled form controls, CSRF tokens on POST forms, and clear empty states.

## Security, deployment, and review

Keep credentials and real portfolio data out of commits, fixtures, logs, and demos. Use local or test databases for verification. Production configuration changes, deployments, and merges require explicit authorization.

Open a focused pull request against the branch that contains the relevant prerequisites. In the PR and completion report, summarize changes, document assumptions and any known gaps, list the exact verification commands and results, and link the PR. Leave it open for review.
