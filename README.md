# Hireform

AI resume builder: guided form → ATS-friendly PDF, five templates, AI bullet rewriting, job-description tailoring, saved versions, email/password auth, Stripe subscription.

**Stack:** FastAPI + SQLite, vanilla JS front end, [RenderCV](https://github.com/rendercv/rendercv) (MIT) as the typesetting engine, Google Gemini (free tier) or Anthropic for AI, Stripe Checkout.
RenderCV is used as a pip dependency, not a fork: the form data becomes RenderCV YAML, `rendercv render` produces the PDF, and nothing in the output carries RenderCV branding. Keep its MIT notice if you ever vendor its source.

## Run locally
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in the values below
uvicorn app:app --reload
```
Open http://localhost:8000, create an account, fill the form, click **Preview PDF**.

## Environment variables
| Name | Purpose |
|---|---|
| `SECRET_KEY` | Signs session cookies. Use a long random string |
| `APP_URL` | Public base URL (`http://localhost:8000` locally). `https://` enables secure cookies |
| `DB_PATH` | SQLite file path (point at a persistent disk in production) |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | AI features via Google AI Studio. Free tier, no card; the model is picked automatically when `GEMINI_MODEL` is empty |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | Alternative AI backend; used only when no Gemini key is set |
| `STRIPE_SECRET_KEY` | `sk_test_…` from the Stripe dashboard (test mode) |
| `STRIPE_PRICE_ID` | A recurring Price (e.g. $9/month); match the price shown on the landing page |
| `STRIPE_WEBHOOK_SECRET` | `whsec_…` for the webhook below |

## Stripe test mode
1. Dashboard (test mode) → Products → add a recurring price → copy its `price_…` id.
2. `stripe listen --forward-to localhost:8000/api/stripe/webhook` → copy the printed `whsec_…`.
3. Click **Upgrade**, pay with card `4242 4242 4242 4242`, any future date/CVC. The webhook flips the account to Pro.

## Deploy
Any host that runs a Docker container works (Render, Fly.io, Railway).
1. Push this folder to GitHub; create a Web Service from the `Dockerfile`.
2. Add a persistent disk (e.g. mounted at `/data`) and set `DB_PATH=/data/app.db`.
3. Set all env vars; set `APP_URL` to your public https URL.
4. In Stripe → Developers → Webhooks, add `https://YOUR-URL/api/stripe/webhook` for `checkout.session.completed` and `customer.subscription.deleted`; put its signing secret in `STRIPE_WEBHOOK_SECRET`.
5. When ready for real money, swap in live Stripe keys and a live Price id.

## Known gaps before charging real customers
Password reset, email verification, rate limiting, Stripe customer portal (cancel/update card), and a move from SQLite to Postgres if you scale past one instance.
