# RAMP: onboarding and KYC drop-off recovery

**Live app: https://ramp-onboard-recovery.streamlit.app/**

Razorpay Campus AI Build-a-thon, Problem 3. A Streamlit app that, for every
onboarding application that has not activated:

1. **diagnoses** why it stalled (reviewer code, else the reviewer's note, else "abandoned at step X"),
2. **decides** whether it is recoverable, closed (restricted category) or sent to compliance,
3. **picks the channel** (WhatsApp only if opted in), ranks by expected TPV and tags urgency by days idle,
4. **drafts the message** in the merchant's preferred language from a pre-written library,
5. **checks every message** for unsafe content and waits for a person to approve, edit or reject it.

No live AI and no API keys: rules, the uploaded rejection catalog and a fixed message library.
Data is synthetic (the build-a-thon Data Pack).

## Try it

Open the [live app](https://ramp-onboard-recovery.streamlit.app/) and click **Load demo data**.
Or upload an applications file and a rejection catalog in the same format (one workbook with both
tabs, or two files; `.xlsx` or `.csv`).

The app runs on Streamlit Community Cloud's free tier, which puts it to sleep after a while without
visitors. If you see a "wake up" button, click it and allow about a minute for it to start.

## Results on the Data Pack

| | |
|---|---|
| Non-activated applications | 75 (49 with a code, 4 with only a note, 22 with neither) |
| Recoverable | 67, expected TPV 933.9 lakh / month |
| Closed (restricted category) | 5 |
| Compliance | 3 |
| Human review | 2 (A3022 borderline and high-value; A3051 closure inferred from the note) |
| Accuracy test (first result, frozen) | 24/24 on the test half; 7 sent to review; 0 wrong and auto-accepted |
| Safety checker | 0/192 false alarms; 45/45 plain unsafe messages caught; 0/7 tricky ones on the first run, 7/7 flagged after the fix |

The accuracy test is small and easy (17 of 24 test notes are copies of tuning notes, and the 7
"new" rows are only 5 distinct sentences), so the true accuracy could be noticeably lower. The
full caveats are in the app's *Tests & log* tab.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Command-line versions (no UI):

```bash
python run_engine.py demo_data/RAMP_demo_P3.xlsx
python run_accuracy_test.py demo_data/RAMP_demo_P3.xlsx
```

## Deploy on Streamlit Community Cloud

1. Push this folder to a GitHub repository (private works).
2. On share.streamlit.io choose *Create app*, pick the repo, branch, and `app.py` as the main file.
3. Under *Advanced settings* choose **Python 3.12** (pandas 3 needs 3.11 or newer).

All paths are relative to `app.py`, so nothing depends on a local machine. The run log is written to
`logs/` inside the app; on Streamlit Cloud that folder resets whenever the app restarts, so download the
log (or the Excel, which includes it) to keep it.

## Layout

| Path | What it does |
|---|---|
| `app.py` | Streamlit UI |
| `ramp/loader.py` | Reads the uploads, finds the tabs, cleans columns, data checks |
| `ramp/catalog.py` | Reads the catalog: outcome per code, abandoned code, keyword weights |
| `ramp/rules.py` | Hand-written keyword rules, each tied to the tuning row it came from |
| `ramp/diagnose.py` | Code → identical coded note → keyword scoring → abandoned |
| `ramp/decide.py` | Outcome, owner, channel, urgency, repeat trier, agent call, review triggers |
| `ramp/message_library.json` | 13 message types in English, Hinglish, Hindi, Tamil, Kannada, Marathi |
| `ramp/messages.py` | Builds each message; switches a template off if the catalog changed its meaning |
| `ramp/checker.py` | Safety checker |
| `ramp/checker_tests.py` | False-alarm test and red-team test of the checker |
| `ramp/evaluate.py`, `ramp/split.py` | Accuracy test (seen vs new phrasings) |
| `ramp/pipeline.py`, `ramp/export.py` | Runs everything; writes the Excel |
| `ramp/config.py` | Every threshold in one place |
| `output/accuracy_first_result.json` | The frozen first accuracy result |

Non-English messages are marked as pending a native-speaker check.

Chart colours: the emphasis blue and gray pass the colour-blind, normal-vision and contrast checks in
light and dark mode (the gray is gray by design: it marks "other causes").
