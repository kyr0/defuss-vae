# Prose catalog

Universal rules for any documentation page in any language. Examples illustrate; the rule decides. **Context** is only what the page, its sources and the repo establish; never invent facts, sources, numbers or names to make a rule pass.

Use: check every changed unit (sentence, paragraph, heading, list, table, diagram) against the rules it touches. A finding is `ID page:line: problem; instruction`. `vae.py prose` already catches the mechanical part (marked *static*); this catalog is the part only reading catches.

## B: evidence and scope of claims

- **B01 Facts.** Is a load-bearing, contested or surprisingly specific claim supported? Name the claim and the evidence it needs. *"Mentoring cuts dropout by 40 %"* with no evaluation → ask for baseline, period and calculation.
- **B02 Coverage.** Does the source cover the claim's population, period, modality and strength? *Pilot with 30 volunteers "could" help short-term* ≠ *"proves it works for everyone, permanently"* → restore the source's scope.
- **B03 Number base.** Are reference value, denominator, sample, comparison, period, unit and method there? Keep relative change and percentage points apart. *20 → 10 errors per 1,000* is "−1 percentage point, −50 % relative", not "fell by 1 %".
- **B04 False precision.** Do decimals, ranks and amounts match the method? Ask for method and uncertainty, mark measurement vs estimate vs scenario; do not round on your own. *"Saves €18,742.63 a year"* with no calculation → ask.
- **B05 Overgeneralization.** Was the group, condition or period widened? *62 % in three branches, Dec 2024* ≠ *"customers prefer self-service"*. Restore only what the source supports.
- **B06 Novelty/superiority.** Is "first", "unique", "leading", "fastest", "revolutionary" backed by a comparison? Ask for the comparison field, or delete the boast and name the actual novelty.
- **B07 Authority.** Are the voices identifiable? One expert is not consensus; "experts agree" with one named source → attribute it to that person.
- **B08 Absence of evidence.** Is "found nothing" turned into "nothing exists" or into a motive? *Empty internal-archive search* → "the documented search found no contributions", not "there is no research".
- **B09 Time/place/version.** Are "current", "latest", "here", "everywhere" pinned to a date, version or place? Do not claim staleness or insert supposedly current knowledge.

## L: logic and argument

- **L01 Inference.** Does the premise carry the conclusion? Name the missing bridge. *"1M downloads, so it is reliable"* → ask for failure rates.
- **L02 Causality.** Is sequence or correlation presented as cause? Check alternatives (seasonality, parallel campaigns, reverse effect); ask for a suitable design or limit the text to the observation.
- **L03 Contradiction.** Same referent, period and measure? Name both places. *"Up all day"* vs *"fully down 14:00 to 16:00"* on the same day → resolve from the log.
- **L04 Scale.** Do dimension, direction and baseline match? *"B is cheaper: B €20, A €10"* → "more expensive". A range of examples needs no ordered scale; a claimed ranking does.
- **L05 Categories.** Are categories exclusive and exhaustive on one criterion? *"Students, employees or under-30s, exactly one each"* mixes dimensions → independent attributes or one rule. Loose examples need no completeness.
- **L06 False dichotomy.** Are the options really exclusive? *"Everyone in the office, or no collaboration"* ignores hybrid. Keep genuine "not X but Y" distinctions.
- **L07 Circularity.** Does the reason restate the claim? *"Reliable because you can rely on it"* → an independent measure. New words do not fix it.
- **L08 Analogy.** Does the shared feature carry the conclusion? Name what does not transfer; illustrative metaphors are fine but are not proof.
- **L09 Logic words.** Are connectives, necessary vs sufficient conditions, possibility vs necessity and quantifiers right? *"With an ID you may enter"* when ID **and** ticket are required → add the ticket.

## P: terms, precision, kind of statement

- **P01 Stable terms.** Are different terms used as synonyms, or one term shifting? *"Collects messages, then archives these errors"* (errors ⊂ messages) → keep "messages". Repetition beats a wrong synonym.
- **P02 Definition.** Is a term the reader needs introduced? *"Processed in batches"* for newcomers → "batches of 100 records". Do not re-define known terms.
- **P03 Referent.** Are pronouns unambiguous? *"Lea wrote to Nora after she read the contract"* → ask who read it.
- **P04 Actors.** Are responsible actors and procedures missing where they matter? *"Approval is granted before shipping"* → who grants it, and how does shipping know? Passive is fine for an irrelevant actor.
- **P05 Abstraction.** Do "optimize", "quality", "sustainable", "balance" have checkable content? Ask for the action, property or measure.
- **P06 Kind of statement.** Are observation, interpretation, judgment, forecast and recommendation (and their speaker) apart? *"The menu is objectively confusing"* in a personal review → "I find the menu confusing".
- **P07 Modality.** Are certainty, frequency, negation, exceptions and reach kept? *Report: "sometimes, on unstable connections"* ≠ *"always"*. Never strip real uncertainty for style.
- **P08 Ambiguity.** Do syntax, ellipsis or attachment allow several readings? *"Applies to staff and managers abroad"*: does "abroad" bind both? Clarify, then write it out.

## R: relevance, substance, use

- **R01 Purpose.** Does the text answer the actual question or an easier neighbor? *"Does it import our CSV files?"* answered with *"digitalization changes work"* → answer the import question.
- **R02 Recommendation.** Who does what, when, under which condition? *"Escalate promptly if needed"* → who, trigger, to whom, deadline. Do not demand a full action plan.
- **R03 Preconditions.** Is a deciding exception, responsibility, side effect or resource unresolved? *"Migrating Friday"* while database access is still pending → clarify first. No arbitrary completeness.
- **R04 Substance.** Does length carry new information, distinction or reasoning? *"A has pros and cons; B too"* → name the deciding difference. Length alone is no defect.
- **R05 False balance.** Equal weight without grounds, or a needed decision avoided? If the agreed criterion is a test A passes and B fails, say so. Respectful weighing is fine.
- **R06 Conclusion/outlook.** Does the conclusion follow, is the forecast grounded? One good quarter does not support a ten-year forecast. Cut needless wrap-ups.
- **R07 Production leftovers.** Assistant address, prompt references, self-praise (*"Here is the carefully crafted article you asked for"*) → delete from publishable text. *static: common phrases*
- **R08 Examples.** Do examples carry the point, and is real vs hypothetical marked? An invented case presented as real → label it "fictional example". Missing evidence does not prove invention; never invent replacement anecdotes.

## A: structure and flow

- **A01 Repetition.** Same idea again without a summarizing, teaching or argumentative job? *"Free to access" / "no fees for access"* → delete one. Exact counts only with every counted place cited.
- **A02 Resumption.** "Already covered" per a summary: check the original passages before calling it duplication.
- **A03 Order.** Is the needed basis visible before a result, term or objection is used? Term defined in section 4, used in section 2 → move the definition up.
- **A04 Headings.** Does the heading match the section or over-promise? *"The solution to all maintenance problems"* over a filter-change procedure → "Filter change in five steps". Fitting creative titles are fine.
- **A05 Triads/lists of examples.** Does every item add a relevant distinction? *"PDF, Portable Document Format and JPEG"* → "PDF (Portable Document Format) and JPEG".
- **A06 List parallelism.** Equal rank and parallel grammar? *"Filling in the form; check the data; sign"* → "Fill in the form; check the data; sign". Mixed content dimensions → L05.
- **A07 Rhythm.** Do repeated openings or uniform paragraphs flatten emphasis? Three times *"Note that"* gives color, a hard requirement and logo position equal weight → state them directly and stress the requirement. Similar sentence length alone is no defect; never add artificial variety.
- **A08 Loops.** Announcement, content and immediate recap of the same point in a short passage → keep the content. Orientation in long or teaching texts stays.

## S: language

- **S01 Nominal style/passive.** Does the form hide a plainly sayable action? *"Execution of the review of the application is carried out by the office"* → "The office reviews the application". Active voice only with a known actor; never add responsibility.
- **S02 Filler.** Does the framing do work? *"It should be noted that the meeting starts at 10"* → "The meeting starts at 10". Keep necessary terminology. *static: common phrases*
- **S03 Pathos.** Do superlatives or adjectives inflate a modest fact? *"An epochal step into a new era: the button is now blue"* → "The button is now blue". Checkable novelty claims → B06. *static: common phrases*
- **S04 Staged contrast.** *"Not just a meeting. A meeting where we set the date"* → "In the meeting we set the delivery date". Keep material negation; false exclusivity → L06.
- **S05 Connector stacking.** "Also", "furthermore", "moreover" framing plain facts as additions → join them directly. Logically wrong connectors → L09.
- **S06 Sentence load.** Do nested clauses, brackets or competing main points block understanding? Untangle into sentences when the meaning is clear; length alone is no defect.
- **S07 Collocation and mixed metaphor.** *"Pull the bottleneck out by the roots and throw it overboard"* → "remove the bottleneck". A guessed origin (translation, machine) is no argument.
- **S08 Register.** Formality, emotion, address and assumed knowledge fit the genre and audience? *"Hey, your application is through, super nice!"* in a formal letter → "Your application has been approved." Do not force blanket dryness.

## T: typography, spelling, presentation

- **T01 Formatting.** Does emphasis serve orientation, or scatter? Bold on five words of one sentence → bold only the deadline. Name the concrete harm, not a count. *static: glyph bullets that do not render as lists*
- **T02 Dashes.** Function, spacing and house style consistent? Mixed dash characters and spacing within one aside → one form. Ranges, signs, names and code stay. *static: em dash and spaced en dash, flagged for a rewrite by meaning (comma, colon, parentheses or two sentences), never a character swap*
- **T03 Spelling/grammar.** Clear errors only, not permitted variants; check commas that change meaning.
- **T04 References and leftovers.** Do section numbers, footnotes, links, brackets and placeholders resolve? "See section 5" in a document with four sections → fix the reference. *static: broken relative links, unclosed fences, `TODO`/`TBD`/`FIXME`, `lorem ipsum`*
- **T05 Number format.** Decimal mark, unit spacing, dates and abbreviations consistent with the house style? Fix presentation only; keep values, magnitudes and units.
- **T06 Special characters.** Do decorative, look-alike or mis-encoded characters harm reading or processing? Cyrillic `о` in an identifier `Kоnto-1` breaks exact matching. Non-ASCII alone is no defect. *static: invisible and bidi characters, mixed-script words, curly quotes and ellipsis (allow them per page where they are the house style, e.g. German `„…“` quotes)*
- **T07 Schematic content.** Is ordered, branching or connected structure (≥ 3 steps with order, branches, loops or feedback; components and connections; states and transitions; actors exchanging messages; a timeline) explained only in prose that a diagram would show at a glance? Use a Mermaid block (`flowchart`, `sequenceDiagram`, `stateDiagram-v2`, `erDiagram`, `timeline`). One diagram, one idea, about 12 nodes at most, quoted labels, and the claim still stated in a sentence. A diagram that repeats a two-item list adds nothing. Render before shipping (`bunx @mermaid-js/mermaid-cli -i page.mmd -o tmp/page.png`) and look at the image. *static: unknown diagram type, unbalanced label quotes*

## Comments and rewrites

1. **A comment names the problem and gives a concrete, justified instruction**, usually one or two sentences: *"Faster than which method, under which load? Add the baseline and a measurement."* Never "more depth" or "phrase better". Mark conditional criticism; never invent problems to cover a rule.
2. **A rewrite replaces the whole quoted unit and is usable as is.** It keeps facts, referents, numbers and units, order, comparison base, causal relations, negation, quantifiers, modality and uncertainty, attribution and protected terminology. No side edits, placeholders, invented sources or measurements, or unchecked names. *"Under high load the service may occasionally respond late"* must not become *"the service is slow"*.
3. **Factual corrections need explicit support from the source, and material caveats survive.** *Log: 10 of 12 tests passed, 2 aborted* → "10 of the 12 tests passed; 2 were aborted", not "all 12 passed". Tightening never deletes a material statement silently; justify meaning-changing deletions.
4. **No rewrite while a factual problem is open**, even when a stylistic fix is obvious: missing facts or evidence, ambiguous meaning, several materially different solutions, a change needed outside the unit, or any check/source/delete action. Comment and ask instead. A deletion is a comment, never an empty replacement. Polishing never replaces clarifying.
