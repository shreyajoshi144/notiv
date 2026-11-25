# Notiv — Case study

**Turning lecture files into study material students can trust.**
Shreya Joshi, builder and project owner · [Live demo](https://notiv-production.up.railway.app) ·
[Repository](https://github.com/shreyajoshi144/notiv)

## The problem

Lectures arrive as a pile of mixed files: a slide deck, a scanned or handwritten PDF, an audio recording, a screen
recording. General AI tools will summarise them, but the student **cannot tell which parts came from the lecture and
which the model made up**. Before an exam, a confident wrong answer is worse than no answer.

## Who has it

* **Students revising:** want a summary, flashcards and a quiz from this week's slides and recording.
* **Students catching up:** want to ask "what did the lecturer say about X?" and jump to that minute.
* **Students with handwritten or scanned notes:** the files most tools read badly or silently skip.

> **Product promise:** every answer and study item links to the exact page or minute it came from. If the lecture
> doesn't contain the answer, Notiv says "not enough evidence" instead of guessing.

## What was cut, and why

| Cut or reduced | Reason |
|---|---|
| Live lecture recording | Real-time capture needs streaming infrastructure. The real pain was the files students already have, so only uploads shipped. |
| YouTube and web links | Unreliable to fetch and raises rights questions. Exactly **4 formats** (PDF, PPTX, MP3, MP4) were accepted and each was tested thoroughly. |
| Google sign-in | Extra configuration and attack surface for little value at this stage. Email and password with secure sessions were kept. |
| 8 export formats → PDF only | Review found too many options, and several produced poor files. One well-designed PDF met the need and cut testing work. |
| Old prototype code | Unused code is a maintenance and security risk. It was removed in the pre-release audit. |
| Guessing unreadable pages | Pages that can't be read reliably are left out and named to the user. An honest gap beats invented content. |
| Larger speech model on the demo | The hosted server ran out of disk. A smaller model kept recordings working within budget; its accuracy is still to be measured. |

Full reasoning: [decision log](DECISION_LOG.md).

## How quality was measured

| What | Result | How |
|---|---|---|
| Real file handling | **33 / 33** edge-case files | Scanned, rotated, hidden slides, noisy and phone-quality audio, vertical video; fake or broken files must be rejected with a clear message |
| Transcription accuracy | Word error **0.0 %** (MP3), **0.8 %** (MP4) | Transcript compared with a known script; timestamps checked to fall inside the recording |
| Answer quality | **100 %** retrieval, correctness, valid citations, refusals | Test set of 3 lectures with known facts and questions they cannot answer; the script blocks a deploy below threshold |
| Citations in production | Checked on **every** item | Fake citations dropped, unsupported sentences removed; if nothing survives, direct quotes are shown |
| Hard real document | **31 / 34** checks | 39 handwritten pages with a garbled text layer; the 3 misses came from the AI vision quota running out |
| Shipped product | **76 / 76** live checks · **636** tests passing | Every upload type on the deployed site, browser end-to-end 18 / 18, automated tests on two database setups |

**Reporting rule:** a number is reported only after the check has actually run; anything untested is marked "not
verified". Limits: test speech is synthetic, the test set is small, and the live AI scorecard has not been re-run since
the API key changed ([QA report §6](QA_TEST_REPORT.md#6-not-verified)).

## Production issues owned

* **Site down (502):** the app and the public domain used different ports. Diagnosed from logs; fixed the settings.
* **Recordings failing:** disk full from the speech model cache. Cleared it and chose a smaller model.
* **Secrets copied into the build image:** caught in the pre-release audit and excluded.

## What's next

* Measure the live setup on real, long recordings, including Hindi / Hinglish lectures.
* Run a usability test with students and have people rate answer accuracy alongside the automatic checks.
* Track which features students use, to decide whether live recording is worth building.

Prioritised list: [roadmap](ROADMAP.md).
