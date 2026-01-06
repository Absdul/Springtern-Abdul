import os
import re
import pandas as pd
import pdfplumber

rows = []
reports = "GraduationSurveyReports"

def clean(x):
    if x is None:
        return ""
    return str(x).replace("\n", " ").strip()

def is_count(x):
    x = clean(x).replace(",", "")
    return x.isdigit()

def is_percent(x):
    x = clean(x)
    return bool(re.match(r"^\d+(\.\d+)?%$", x))

def looks_like_label(s):
    s = clean(s)
    if not s:
        return False
    if is_count(s) or is_percent(s):
        return False
    # ignore common header tokens
    if s.lower() in {"outcome", "#", "%"}:
        return False
    return True

def find_label_anywhere(row):
    labels = [clean(c) for c in row if looks_like_label(c)]
    return max(labels, key=len) if labels else ""

def find_count_anywhere(row):
    for cell in row:
        if is_count(cell):
            return clean(cell)
    return ""

def find_percent_anywhere(row):
    for cell in row:
        if is_percent(cell):
            return clean(cell)
    return ""

def title_line_candidate(line: str) -> bool:
    up = line.upper()
    if not line.strip():
        return False
    # skip obvious non-titles
    bad_starts = (
        "SURVEY RESPONSE RATE",
        "KNOWLEDGE RATE",
        "TOTAL PLACEMENT",
        "REPORTED OUTCOMES",
        "GRADUATE OUTCOMES",
        "AS OF ",
    )
    if any(up.startswith(b) for b in bad_starts):
        return False
    # must have letters
    return any(ch.isalpha() for ch in line)

def get_page_title(page):
    text = page.extract_text() or ""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

    # Lines we never want to treat as "title"
    bad_contains = (
        "survey response rate",
        "knowledge rate",
        "total placement",
        "reported outcomes",
        "graduate outcomes",
        "as of",
        "data from",
        "had been collected",
        "via the survey",
        "between",  # <-- this is the big one causing your issue
    )

    def is_good_title_line(ln: str) -> bool:
        low = ln.lower()

        # reject body/metric lines
        if any(b in low for b in bad_contains):
            return False

        # reject lines with obvious table symbols or percents
        if "%" in ln or "#" in ln:
            return False

        # reject long sentences (usually paragraph text)
        if len(ln) > 80:
            return False

        # reject lines with lots of digits (also usually body)
        if sum(ch.isdigit() for ch in ln) >= 2:
            return False

        # must have letters
        return any(ch.isalpha() for ch in ln)

    # Find the first good title candidate near the top
    start_idx = None
    for i, ln in enumerate(lines[:20]):
        if is_good_title_line(ln):
            start_idx = i
            break

    if start_idx is None:
        return ""

    # Join 1–3 continuation lines if they also look like title lines
    title_lines = [lines[start_idx]]
    for j in range(start_idx + 1, min(start_idx + 4, len(lines))):
        if is_good_title_line(lines[j]) and len(lines[j]) <= 60:
            title_lines.append(lines[j])
        else:
            break

    return " ".join(title_lines)

def is_outcomes_table(table):
    """
    Identify the right table without relying on a header row.
    We score tables by how many rows look like: label + count + percent.
    Also require it contains outcome-type keywords to avoid grabbing
    unrelated % tables (like continuing education breakdowns).
    """
    score = 0
    labels = []

    for r in table[:15]:
        if not r:
            continue
        lab = find_label_anywhere(r).lower()
        cnt = find_count_anywhere(r)
        pct = find_percent_anywhere(r)

        if lab:
            labels.append(lab)
        if lab and cnt and pct:
            score += 1

    # must have several real rows
    if score < 3:
        return False, score

    # outcome table usually contains at least one of these concepts
    if not any(("employed" in l or "unplaced" in l or "unresolved" in l) for l in labels):
        return False, score

    return True, score

def parse_outcomes_table(table):
    """
    Parse rows while fixing wrapped labels.
    Key fix: if a row has label text but NO numbers, append it to the previous outcome.
    """
    out = []
    last = None
    pending_label = ""

    for r in table:
        if not r:
            continue

        label = find_label_anywhere(r)
        cnt = find_count_anywhere(r)
        pct = find_percent_anywhere(r)

        # 1) Continuation line: label exists, but no numbers
        if label and not cnt and not pct:
            if last is not None:
                last["outcome"] = (last["outcome"] + " " + label).strip()
            else:
                pending_label = (pending_label + " " + label).strip()
            continue

        # 2) If label is missing but we have pending text, use it
        if not label and pending_label:
            label = pending_label
            pending_label = ""

        # 3) If both exist, merge pending + current label
        if label and pending_label:
            label = (pending_label + " " + label).strip()
            pending_label = ""

        if not label:
            continue

        # skip header-ish rows
        if label.lower() == "outcome":
            continue

        # 4) Keep rows with a count. Percent is optional (Not Seeking often has no %)
        if cnt and (pct or label.lower().startswith("not seeking")):
            row = {"outcome": label, "count": cnt, "percent": pct}
            out.append(row)
            last = row
        else:
            # if it's text but still no count, treat as pending
            if label and not cnt:
                pending_label = (pending_label + " " + label).strip()

    return out

def extract_total_and_not_seeking_from_text(page_text):
    """
    Newer PDFs sometimes don't include TOTAL / Not Seeking inside extract_tables().
    Pull them from the raw extracted text as a fallback.
    """
    found = []

    # TOTAL 1072 100.0%
    m_total = re.search(r"\bTOTAL\b\s+([\d,]+)\s+(\d+(\.\d+)?%)", page_text, re.IGNORECASE)
    if m_total:
        found.append({"outcome": "TOTAL", "count": m_total.group(1), "percent": m_total.group(2)})

    # Not Seeking 10   (no percent)
    m_ns = re.search(r"\bNot\s+Seeking\b\s+([\d,]+)\b", page_text, re.IGNORECASE)
    if m_ns:
        found.append({"outcome": "Not Seeking", "count": m_ns.group(1), "percent": ""})

    return found

for file in os.listdir(reports):
    if not file.endswith(".pdf"):
        continue

    new_path = os.path.join(reports, file)

    with pdfplumber.open(new_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables() or []
            if not tables:
                continue

            page_title = get_page_title(page)
            page_text = page.extract_text() or ""

            # pick best matching outcomes table on this page
            candidates = []
            for t in tables:
                if not t:
                    continue
                ok, score = is_outcomes_table(t)
                if ok:
                    candidates.append((score, t))

            if not candidates:
                continue

            candidates.sort(key=lambda x: x[0], reverse=True)
            best_table = candidates[0][1]

            parsed = parse_outcomes_table(best_table)

            # fallback for TOTAL / Not Seeking if missing
            existing_outcomes = {p["outcome"].lower() for p in parsed}
            for extra in extract_total_and_not_seeking_from_text(page_text):
                if extra["outcome"].lower() not in existing_outcomes:
                    parsed.append(extra)

            for item in parsed:
                rows.append({
                    "pdf": file,
                    "title": page_title,
                    "outcome": item["outcome"],
                    "count": item["count"],
                    "percent": item["percent"],
                })

df = pd.DataFrame(rows).drop_duplicates()
df.to_csv("outcome_week1.csv", index=False)

