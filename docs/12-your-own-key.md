# 12. Your own key and the dashboard

Running the agents costs money: every step is a call to an AI company, and they charge for it. On the public site there are three ways to see it work.

| Who | How | Who pays |
|---|---|---|
| Anyone | Watch a saved run. It replays a real run I recorded, step by step. | Nobody. No AI is called. |
| Anyone with an API key | Paste your own key from an AI provider, then run any alert live. | You, on your own account. Most providers on the list have a free tier. |
| Me, and people I give it to | The demo code. | Me, on my Groq key. |

## Bringing your own key

An **API key** is a long password that an AI company gives you so your programs can use its models. It looks like `gsk_...` for Groq or `sk-...` for OpenAI.

On the **Try it** tab, under the alert, there is a box called **Run it live with your own key**:

1. Type your name.
2. Pick your provider. The ones with a free tier are at the top: Groq, Google Gemini, OpenRouter, Cerebras, Mistral and Cohere. Paid ones follow: OpenAI, Anthropic, DeepSeek, xAI and Together AI. Each one has a link to where you get a key.
3. Paste the key and press **Check my key**.
4. If the provider accepts it, you pick a model from the ones your key can use, and the Start button turns into **Run the agents live**.

All eight steps then run on your model. The one exception is the safety check in step 1: it uses Llama Prompt Guard 2, which only Groq offers, so it only runs when you use a Groq key.

## What happens to your key

This is the part that matters most, so here it is exactly.

**Before anything is sent anywhere**, the server checks the key on its own:

- **The provider must be on the list.** Each provider's web address is written into the code (`providers.py`). You choose a provider by name. You can never type in an address, so the server only ever sends your key to the company that made it.
- **The key must be plain.** Letters, numbers and `- _ .` only, 20 to 256 characters. Anything else is refused, because spaces, quotes or line breaks could be used to sneak extra instructions into a web request.
- **The key must look like that provider's keys.** A Groq key starts with `gsk_`. If you pick OpenAI and paste a Groq key, you're told straight away.

**Then the provider itself is asked.** The server makes one free request to the provider (listing its models, which uses no tokens). Only if the provider says yes does a run start. The server never follows a redirect on these requests, so a key can't be bounced somewhere else.

**While it runs**, the key lives in the server's memory, inside one connection to your provider. It travels from your browser in a request header, never in a web address (addresses end up in logs and browser history). Your browser keeps it in memory too, not in storage, so reloading the page forgets it.

**After the run**, the connection is closed and the key is gone.

**It is never written down.** Not in the database, not in the logs, not in LangSmith, not in any error message. If a provider's error message repeats the key back, the server cuts it out before anyone sees it. There are tests for each of these.

What *is* kept, so the dashboard can show which providers people use:

| Kept | Example | Why it's safe |
|---|---|---|
| A masked copy | `gsk_••••••••a1b2` | Only the first and last 4 characters, the way providers show keys in their own consoles. It can't be used for anything. |
| A fingerprint | `7f3a9c21b04e` | A one-way code made from the key and a secret only the server knows. It tells you when the same key comes back, but it can't be turned back into the key. |

## Limits

These stop one person from using up the shared parts (the Pinecone search and the LangSmith logbook, which are on my free plans):

| Limit | Number |
|---|---|
| Live runs per visitor | 10 per hour |
| Live runs per key | 20 per hour |
| Live runs for the whole site | 300 per day |
| Key checks per visitor | 20 per hour |

Limits are counted in a small shared database (**Upstash Redis**), so they hold even though the site runs on many short-lived serverless copies at once. A visitor is counted by a fingerprint of their address, not the address itself.

## The dashboard

At `/admin` there is a page only I can open. It shows:

- how many live runs visitors made, how many different visitors and keys, and how many runs finished;
- each provider's share of the runs, and how many different keys came from it;
- runs per day for the last two weeks;
- a table of every key check and run: time, name, provider, model, masked key and fingerprint, what kind of alert, the result or error, the techniques found, tokens, time taken, country and city (from Vercel), and browser.

It has a search box, filters, and a CSV download.

### How the login works

There is one username and one password. The password itself is never stored anywhere on the server. Vercel only has a **hash** of it, made with scrypt, a method that is deliberately slow so guesses are expensive.

When I sign in, the server gives my browser a **session cookie** that:

- is signed, so it can't be faked or changed;
- lasts 8 hours;
- is `HttpOnly`, so JavaScript on the page can't read it;
- is `SameSite=Strict`, so other websites can't use it;
- is only sent to `/api/admin`.

Changing the password signs every session out at once. Sign-in attempts are limited to 5 per 15 minutes per visitor and 30 per hour in total, and sign-ins coming from another website are refused.

`scripts/make_admin.py` creates the login and saves it to my secrets file. It never prints the password.

## The rest of the protection

Every page is sent with headers that tell the browser:

- **Content-Security-Policy:** only run scripts from this site (and one small inline script, pinned by its hash), only connect back to this site, and never let this site be shown inside another one;
- **Strict-Transport-Security:** always use HTTPS;
- **X-Content-Type-Options**, **Referrer-Policy**, **Permissions-Policy:** the usual extra locks.

The dashboard is also marked so search engines don't list it.

Next: back to the [index](README.md)
