import os
import re
import pandas as pd
import pdfplumber
import numpy as np

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
    x = clean(x).replace(" ", "")
    return bool(re.match(r"^<?\d+(\.\d+)?%$", x))

def looks_like_label(s):
    s = clean(s)
    if not s:
        return False
    if is_count(s) or is_percent(s):
        return False

    low = s.lower()

    if low in {"outcome", "#", "%"}:
        return False

    if "reported outcomes" in low or "graduate outcomes" in low:
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

    return any(ch.isalpha() for ch in line)

def get_page_title(page):
    text = page.extract_text() or ""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

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
        "between",
    )

    def is_good_title_line(ln: str) -> bool:
        low = ln.lower()

        if any(b in low for b in bad_contains):
            return False

        if "%" in ln or "#" in ln:
            return False

        if len(ln) > 80:
            return False

        if sum(ch.isdigit() for ch in ln) >= 2:
            return False

        return any(ch.isalpha() for ch in ln)

    start_idx = None
    for i, ln in enumerate(lines[:10]):
        if is_good_title_line(ln):
            start_idx = i
            break

    if start_idx is None:
        return ""

    title_lines = [lines[start_idx]]
    for j in range(start_idx + 1, min(start_idx + 4, len(lines))):
        if is_good_title_line(lines[j]) and len(lines[j]) <= 60:
            title_lines.append(lines[j])
        else:
            break

    return " ".join(title_lines)

def is_outcomes_table(table):
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

    if score < 3:
        return False, score

    if not any(("employed" in l or "unplaced" in l or "unresolved" in l) for l in labels):
        return False, score

    return True, score

def parse_outcomes_table(table):
    out = []
    last = None
    pending_label = ""

    for r in table:
        if not r:
            continue

        label = find_label_anywhere(r)
        low = label.lower()
        junk_phrases = [
            "reported outcomes of graduates",
            "reported outcomes of",
            "graduate outcomes",
        ]
        for jp in junk_phrases:
            if jp in low:
                label = re.sub(jp, "", label, flags=re.IGNORECASE).strip()
                low = label.lower()

        label = re.sub(r"\b20\d{2}\s+graduates\b", "", label, flags=re.IGNORECASE).strip()

        label = re.sub(r"\s{2,}", " ", label).strip()
        cnt = find_count_anywhere(r)
        pct = find_percent_anywhere(r)

        if label and not cnt and not pct:
            if last is not None:
                last["outcome"] = (last["outcome"] + " " + label).strip()
            else:
                pending_label = (pending_label + " " + label).strip()
            continue

        if not label and pending_label:
            label = pending_label
            pending_label = ""

        if label and pending_label:
            label = (pending_label + " " + label).strip()
            pending_label = ""

        if not label:
            continue

        if label.lower() == "outcome":
            continue

        is_total = label.strip().lower() == "total"
        is_not_seeking = label.strip().lower().startswith("not seeking")

        if cnt and (pct or is_total or is_not_seeking):
            row = {"outcome": label, "count": cnt, "percent": pct}
            out.append(row)
            last = row
        else:
            if label and not cnt:
                pending_label = (pending_label + " " + label).strip()

    return out

def extract_total_and_not_seeking_from_text(page_text):
    found = []

    m_total = re.search(r"\bTOTAL\b\s+([\d,]+)(?:\s+(\d+(\.\d+)?%))?", page_text, re.IGNORECASE)
    if m_total:
        found.append({"outcome": "TOTAL", "count": m_total.group(1), "percent": m_total.group(2) or ""})

    m_ns = re.search(r"\bNot\s+Seeking\b\s+([\d,]+)\b", page_text, re.IGNORECASE)
    if m_ns:
        found.append({"outcome": "Not Seeking", "count": m_ns.group(1), "percent": ""})

    return found

def outcome_key(s: str):
    s = (s or "").lower()

    if "employed" in s and "ft" in s: return "Employed FT"
    if "employed" in s and "pt" in s: return "Employed PT"
    if "continuing" in s and "education" in s: return "Continuing Edu"
    if "volunteer" in s or "service program" in s: return "Volunteering"
    if "military" in s: return "Military"
    if "business" in s: return "Business"
    if "unplaced" in s: return "Unplaced"
    if "unresolved" in s: return "Unresolved"
    if re.search(r"\btotal\b", s): return "Total"
    if "not seeking" in s: return "Not Seeking"
    return None

def pct_to_float(x):
    x = str(x).strip()

    if not x:
        return np.nan

    if x.startswith("<") and x.endswith("%"):
        return 1.0

    try:
        return float(x[:-1]) 
    except ValueError:
        return np.nan
    
def normalize_unit(u: str) -> str:
    u = str(u or "").strip()
    u = re.sub(r"\s+", " ", u)        
    u = u.replace("–", "-").replace("—", "-")

    low = u.lower()

    if "university of maryland" in low and (
        "university-wide" in low
        or "university wide" in low
        or "overall" in low
        or "graduate survey report" in low):
        return "University-wide"

    if low == "university of maryland":
        return "University-wide"

    return u

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

df = pd.DataFrame(rows)
df["Year"] = df["pdf"].str.extract(r"(20\d{2})").astype("Int64")

df = df.rename(columns={"title": "Unit"})

df["count"] = df["count"].astype(str).str.replace(",", "", regex=False)
df.loc[~df["count"].str.fullmatch(r"\d+"), "count"] = np.nan
df["count"] = df["count"].astype("Int64")

df["percent"] = df["percent"].fillna("").astype(str).str.replace(" ", "", regex=False)

df["Outcome"] = df["outcome"].apply(outcome_key)
df = df[df["Outcome"].notna()].copy()

n = df.pivot_table(index=["Unit", "Year"], columns="Outcome", values="count", aggfunc="first")
p = df.pivot_table(index=["Unit", "Year"], columns="Outcome", values="percent", aggfunc="first")

out = pd.concat([n.add_suffix(" N"), p.add_suffix(" %")], axis=1).reset_index()

unplaced = out.get("Unplaced %", "").apply(pct_to_float) if "Unplaced %" in out else np.nan
unresolved = out.get("Unresolved %", "").apply(pct_to_float) if "Unresolved %" in out else np.nan
if "Unplaced %" in out and "Unresolved %" in out:
    out["Placement Rate %"] = (100 - unplaced - unresolved).round(1).astype(str) + "%"

col_order = [
    "Unit","Year",
    "Employed FT N","Employed FT %",
    "Employed PT N","Employed PT %",
    "Continuing Edu N","Continuing Edu %",
    "Volunteering N","Volunteering %",
    "Military N","Military %",
    "Business N","Business %",
    "Unplaced N","Unplaced %",
    "Unresolved N","Unresolved %",
    "Total N",
    "Not Seeking N",
    "Placement Rate %",
]
out = out[[c for c in col_order if c in out.columns]]

out["Unit"] = out["Unit"].astype(str).str.strip()
out["Unit"] = out["Unit"].str.replace(r"\s+", " ", regex=True)  
out["Unit"] = out["Unit"].str.replace(" ,", ",", regex=False) 

out["Unit"] = out["Unit"].str.replace(
    r"(?i)^university of maryland\s*-\s*overall$",
    "University-wide",
    regex=True
)

unit_order = [
    "University-wide",
    "College of Agriculture and Natural Resources",
    "College of Arts and Humanities",
    "College of Behavioral and Social Sciences",
    "College of Computer, Mathematical, and Natural Sciences",
    "College of Education",
    "College of Information",
    "The A. James Clark School of Engineering",
    "Philip Merrill College of Journalism",
    "School of Architecture, Planning, and Preservation",
    "School of Public Health",
    "School of Public Policy",
    "The Robert H. Smith School of Business",
    "College Park Scholars",
    "Honors College",
    "Letters and Sciences",
    "Undergraduate Studies",
]
out["Unit"] = out["Unit"].apply(normalize_unit)
out["Unit_cat"] = pd.Categorical(out["Unit"], categories=unit_order, ordered=True)

out = (
    out.sort_values(
        ["Year", "Unit_cat", "Unit"], 
        ascending=[True, True, True],
        na_position="last"
    )
    .drop(columns=["Unit_cat"])
    .reset_index(drop=True)
)

out.to_csv("outcome_week1.csv", index=False)