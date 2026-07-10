#!/usr/bin/env python3
"""Community Report analyzer v2 — pure counting, no models, no API calls.

Distinctive vocabulary is scored against a general-English frequency list
(english-freq.txt — Norvig's count_1w.txt, auto-downloaded if missing), so the
lexicon shows what this community says that English at large does not.

Usage:
  python3 report.py --archive DIR [DIR ...]     # dirs containing *.jsonl
  python3 report.py                             # falls back to feed/feed.json
"""
import json, glob, os, re, sys, collections, statistics, urllib.request
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "report")
ENG_PATH = os.path.join(BASE, "english-freq.txt")
ENG_URL = "https://norvig.com/ngrams/count_1w.txt"
NAMES_PATH = os.path.join(BASE, "first-names.txt")
NAMES_URL = "https://raw.githubusercontent.com/dominictarr/random-name/master/first-names.txt"

WORD = re.compile(r"[a-z][a-z'\-]{2,}")
URL_RE = re.compile(r"https?://\S+")
MARKUP = re.compile(r"<a?(:\w+:)\d+>|<[@#][!&]?\d+>")
MENTION = re.compile(r"<@!?(\d+)>")

# words every online community over-uses; not a fingerprint of anyone
PLATFORM = set("""discord substack github twitter tweet tweets tweeting reddit youtube tiktok
instagram facebook whatsapp telegram signal slack zoom google gmail email emails dm dms ping
pinged server servers channel channels thread threads bot bots online offline post posts posted
posting repost blog blogs blogpost link links website websites site sites app apps webpage web
internet wifi username usernames profile profiles avatar login notification notifications feed
feeds subscribe subscribed subscriber subscribers follow follows following followers unfollow
upvote upvotes downvote comment comments commented meme memes gif gifs emoji emojis hashtag
stream streaming podcast podcasts spotify patreon paypal venmo crypto bitcoin eth url urls
browser chrome firefox android iphone ios laptop desktop pc keyboard screenshot screenshots
video videos audio mic camera call calls calling voice vc chat chats chatting message messages
messaged messaging group groups admin admins mod mods moderator lurker lurkers""".split())

RECENT_MONTHS = 3          # window that defines "emerging" / "dying"
MIN_DISTINCT = 8           # min occurrences to enter the lexicon
FLOOR = 1e-8               # english rate floor for out-of-vocabulary words


def load_english():
    if not os.path.exists(ENG_PATH):
        print("downloading english frequency list…")
        urllib.request.urlretrieve(ENG_URL, ENG_PATH)
    rates, total, common = {}, 0, set()
    with open(ENG_PATH) as fh:
        for i, line in enumerate(fh):
            w, c = line.split("\t")
            rates[w] = int(c)
            total += int(c)
            if i < 20000:
                common.add(w)          # guards name-detection: "will", "grace", "art"
    return {w: c / total for w, c in rates.items()}, common


def load_first_names():
    try:
        if not os.path.exists(NAMES_PATH):
            urllib.request.urlretrieve(NAMES_URL, NAMES_PATH)
        return {line.strip().lower() for line in open(NAMES_PATH) if line.strip()}
    except OSError:
        return set()


def emojis_of(text):
    return [ch for ch in text for o in (ord(ch),)
            if 0x1F000 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF or 0x2B00 <= o <= 0x2BFF]


def month_add(month, k):
    y, m = int(month[:4]), int(month[5:7]) + k
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


class Agg:
    def __init__(self):
        self.msgs = 0
        self.tokens = 0
        self.words = collections.Counter()
        self.monthly = collections.defaultdict(collections.Counter)
        self.first_seen = {}                 # word -> (ts, user, url)
        self.last_seen = {}                  # word -> ts
        self.speakers = collections.defaultdict(set)
        self.authors = collections.Counter()
        self.author_first = {}               # user -> month
        self.author_months = collections.defaultdict(set)
        self.channels = set()
        self.heat = [[0] * 24 for _ in range(7)]
        self.emoji = collections.Counter()
        self.questions = 0
        self.q_ids = []                      # ids of question messages
        self.media = 0
        self.links = 0
        self.lengths = collections.Counter() # bucket -> n
        self.ts_of = {}                      # id -> ts
        self.reply_pairs = []                # (child_ts, parent_id)
        self.replied = set()                 # parent ids that got replies
        self.replies_received = collections.Counter()
        self.author_of = {}                  # id -> user
        self.threads = collections.Counter() # (channel, thread) -> n
        self.thread_last = {}                # key -> (ts, user)
        self.mention_ids = collections.Counter()
        self.tmin = None
        self.tmax = None

    def feed(self, m):
        self.msgs += 1
        user = m.get("user") or "?"
        mid = m.get("id")
        t = m.get("timestamp") or ""
        month = t[:7]
        self.authors[user] += 1
        if month:
            if user not in self.author_first or month < self.author_first[user]:
                self.author_first[user] = month
            self.author_months[user].add(month)
        self.channels.add(m.get("channel") or "?")
        if mid:
            self.ts_of[mid] = t
            self.author_of[mid] = user
        if m.get("reply_to"):
            self.reply_pairs.append((t, m["reply_to"]))
            self.replied.add(m["reply_to"])
        if t:
            self.tmin = min(self.tmin or t, t)
            self.tmax = max(self.tmax or t, t)
            try:
                dt = datetime.fromisoformat(t).astimezone(timezone.utc)
                self.heat[dt.weekday()][dt.hour] += 1
            except ValueError:
                pass
        text = m.get("message") or ""
        for uid in MENTION.findall(text):
            self.mention_ids[uid] += 1
        for e in MARKUP.findall(text):
            self.emoji[e] += 1
        for e in emojis_of(text):
            self.emoji[e] += 1
        for r in m.get("reactions", []) or []:
            e = r.get("emoji") or "?"
            self.emoji[e if len(e) <= 2 else f":{e}:"] += r.get("count", 1)
        clean = URL_RE.sub(" ", MARKUP.sub(" ", text)).lower()
        toks = WORD.findall(clean)
        self.tokens += len(toks)
        for w in toks:
            self.words[w] += 1
            self.speakers[w].add(user)
            if month:
                self.monthly[w][month] += 1
            if w not in self.first_seen or (t and t < self.first_seen[w][0]):
                self.first_seen[w] = (t, user, m.get("url"))
            self.last_seen[w] = max(self.last_seen.get(w, ""), t)
        n = len(toks)
        bucket = ("1-5" if n <= 5 else "6-15" if n <= 15 else
                  "16-40" if n <= 40 else "41-100" if n <= 100 else "100+")
        self.lengths[bucket] += 1
        if "?" in text:
            self.questions += 1
            if mid:
                self.q_ids.append(mid)
        if m.get("attachments"):
            self.media += 1
        if URL_RE.search(text):
            self.links += 1

    def dump(self, eng, common, name_tokens, display_of, first_names):
        total = max(1, self.tokens)
        rate = lambda w: self.words[w] / total

        def eng_rate(w):
            return eng.get(w) or eng.get(w.replace("'", "").replace("-", "")) or 0.0

        def is_person(w):
            return w in name_tokens or (w in first_names and w not in common)

        # ── most mentioned people: name tokens + <@id> mentions ─────────────
        mentioned = collections.Counter()
        for w, c in self.words.items():
            if is_person(w):
                mentioned[name_tokens.get(w, w.capitalize())] += c
        for uid, c in self.mention_ids.items():
            name = display_of.get(uid)
            if name:
                mentioned[name] += c

        # ── distinctive lexicon: ratio vs English, people/platform removed ──
        scored = []
        for w, c in self.words.items():
            if c < MIN_DISTINCT or len(w) < 3 or "'" in w:
                continue
            if is_person(w) or w in PLATFORM:
                continue
            er = eng_rate(w)
            score = rate(w) / max(er, FLOOR)
            scored.append((w, c, round(score, 1), er == 0.0,
                           len(self.speakers[w])))
        scored.sort(key=lambda x: -x[2])
        # two species: coined (not in English at all) vs repurposed (common
        # word at an anomalous rate)
        coined = [x for x in scored if x[3]][:12]
        repurposed = [x for x in scored if not x[3]][:12]
        lexicon = scored[:25]

        # ── words over time: top distinctive words with enough spread ───────
        top6 = [w for w, *_ in lexicon if len(self.monthly[w]) >= 2][:6]
        months = sorted({mo for w in self.monthly for mo in self.monthly[w]})
        series = {w: [self.monthly[w].get(mo, 0) for mo in months] for w in top6}

        # ── emerging / dying ────────────────────────────────────────────────
        recent_cut = month_add(months[-1], -(RECENT_MONTHS - 1)) if months else ""
        emerging, dying = [], []
        for w, c in self.words.items():
            if len(w) < 3 or c < 5 or "'" in w:
                continue
            if is_person(w) or w in PLATFORM:
                continue
            if rate(w) / max(eng_rate(w), FLOOR) < 3:   # must be distinctive too
                continue
            fs, ls = self.first_seen[w][0][:7], self.last_seen[w][:7]
            if fs >= recent_cut and len(self.speakers[w]) >= 2:
                emerging.append((w, c, self.first_seen[w][0][:10],
                                 len(self.speakers[w])))
            elif c >= 15 and ls < recent_cut:
                dying.append((w, c, self.last_seen[w][:10]))
        emerging.sort(key=lambda x: -x[1])
        dying.sort(key=lambda x: -x[1])

        # ── patient zero: adoption cascades among distinctive words ─────────
        pz = []
        for w, c, score, new, nspeak in lexicon:
            if nspeak >= 3:
                ts, user, url = self.first_seen[w]
                pz.append({"word": w, "first_user": user, "date": ts[:10],
                           "adopters": nspeak, "count": c, "url": url})
            if len(pz) == 8:
                break

        # ── conversation culture ─────────────────────────────────────────────
        deltas = []
        for child_ts, pid in self.reply_pairs:
            pts = self.ts_of.get(pid)
            if pts and child_ts:
                try:
                    d = (datetime.fromisoformat(child_ts)
                         - datetime.fromisoformat(pts)).total_seconds() / 60
                    if 0 <= d < 60 * 24 * 7:
                        deltas.append(d)
                except ValueError:
                    pass
        latency = round(statistics.median(deltas), 1) if deltas else None
        tc = sorted(self.threads.values())
        depth = {"median": tc[len(tc)//2] if tc else 0,
                 "p90": tc[int(len(tc)*0.9)] if tc else 0,
                 "deep_share": round(sum(1 for x in tc if x >= 10) / len(tc), 3) if tc else 0}
        unanswered = sum(1 for q in self.q_ids if q not in self.replied)
        m = max(1, self.msgs)
        sample = min(total, 20000)
        style = {"lengths": [ [b, self.lengths.get(b, 0)] for b in
                              ["1-5", "6-15", "16-40", "41-100", "100+"] ],
                 "question_ratio": round(self.questions / m, 3),
                 "unanswered_ratio": round(unanswered / max(1, self.questions), 3),
                 "link_ratio": round(self.links / m, 3),
                 "media_ratio": round(self.media / m, 3),
                 "richness": round(len(self.words) / total * 1000, 1)}

        # ── new voices & retention ───────────────────────────────────────────
        first_by_month = collections.Counter(self.author_first.values())
        voices_series = [first_by_month.get(mo, 0) for mo in months]
        kept = tried = 0
        for user, fm in self.author_first.items():
            if months and fm < months[-1]:
                tried += 1
                if month_add(fm, 1) in self.author_months[user]:
                    kept += 1
        retention = round(kept / tried, 3) if tried else None

        # ── spark & last word ────────────────────────────────────────────────
        for pid in self.replied:
            if pid in self.author_of:
                self.replies_received[self.author_of[pid]] += 1
        spark = self.replies_received.most_common(5)
        last_word = collections.Counter(u for _, u in self.thread_last.values()).most_common(5)

        return {
            "stats": {"messages": self.msgs, "voices": len(self.authors),
                      "channels": len(self.channels),
                      "from": (self.tmin or "")[:10], "to": (self.tmax or "")[:10]},
            "lexicon": [[w, c, s, new, ns] for w, c, s, new, ns in lexicon],
            "coined": [[w, c, s] for w, c, s, _, _ in coined],
            "repurposed": [[w, c, s] for w, c, s, _, _ in repurposed],
            "mentioned": mentioned.most_common(12),
            "rates_per_million": {w: round(rate(w) * 1e6, 2)
                                  for w, c, *_ in scored[:300]},
            "timeline": {"months": months, "series": series},
            "emerging": emerging[:10], "dying": dying[:10],
            "patient_zero": pz,
            "culture": {"latency_min": latency, "depth": depth, **style},
            "voices": {"months": months, "new": voices_series, "retention": retention},
            "spark": spark, "last_word": last_word,
            "heatmap": self.heat,
            "emojis": self.emoji.most_common(12),
        }

    def feed_thread(self, m):
        key = (m.get("channel"), m.get("thread"))
        if m.get("thread"):
            self.threads[key] += 1
            t = m.get("timestamp") or ""
            if key not in self.thread_last or t > self.thread_last[key][0]:
                self.thread_last[key] = (t, m.get("user") or "?")


def main():
    records = []
    if "--archive" in sys.argv:
        for d in sys.argv[sys.argv.index("--archive") + 1:]:
            for f in glob.glob(os.path.join(os.path.expanduser(d), "*.jsonl")):
                with open(f) as fh:
                    records.extend(json.loads(line) for line in fh)
    else:
        records = json.load(open(os.path.join(BASE, "feed", "feed.json")))["messages"]

    eng, common = load_english()
    first_names = load_first_names()

    # member identity: token -> display name, discord id -> display name
    name_tokens, display_of = {}, {}
    for m in records:
        user = m.get("user") or "?"
        uid = (m.get("user_id") or "").split(":")[-1]
        if uid:
            display_of[uid] = user
        low = user.lower()
        name_tokens[re.sub(r"[^a-z0-9]", "", low)] = user
        for part in WORD.findall(low):
            if len(part) >= 4 and part not in common:
                name_tokens[part] = user

    aggs = collections.defaultdict(Agg)
    for m in records:
        for key in ("__all__", m.get("server") or "server"):
            aggs[key].feed(m)
            aggs[key].feed_thread(m)

    args = (eng, common, name_tokens, display_of, first_names)
    out = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "combined": aggs.pop("__all__").dump(*args),
           "servers": {name: a.dump(*args) for name, a in sorted(aggs.items())}}
    os.makedirs(OUT, exist_ok=True)
    json.dump(out, open(os.path.join(OUT, "report.json"), "w"), ensure_ascii=False)
    print(f"{len(records)} messages -> report/report.json")
    print("top lexicon:", [w for w, *_ in out["combined"]["lexicon"][:12]])

if __name__ == "__main__":
    main()
