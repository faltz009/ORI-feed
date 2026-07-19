"""Model-free language analysis for the ORI community report.

The analyzer consumes canonical feed records one message at a time. It keeps
only counters: a current window for Memetic Weather and a longer weekly layer
for linguistic movement. Participant and channel attribution are retained only
as compact aggregates so the report can answer who or where a signal came from
without copying message text into the report.
"""

from __future__ import annotations

import collections
import hashlib
import itertools
import math
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse


WORD = re.compile(r"[a-z][a-z'\-]{2,}")
URL = re.compile(r"https?://[^\s<>]+", re.I)
DISCORD_MARKUP = re.compile(r"<a?:\w+:\d+>|<[@#][!&]?\d+>")
CUSTOM_EMOJI = re.compile(r"<a?:([A-Za-z0-9_]+):\d+>")

STOPWORDS = set("""
a about above after again against all am an and any are aren't as at be because
been before being below between both but by can can't cannot could couldn't did
didn't do does doesn't doing don't down during each few for from further had
hadn't has hasn't have haven't having he he'd he'll he's her here here's hers
herself him himself his how how's i i'd i'll i'm i've if in into is isn't it
it's its itself just let's me more most mustn't my myself no nor not of off on
once only or other ought our ours ourselves out over own same she she'd she'll
she's should shouldn't so some such than that that's the their theirs them
themselves then there there's these they they'd they'll they're they've this
those through to too under until up very was wasn't we we'd we'll we're we've
were weren't what what's when when's where where's which while who who's whom
why why's with won't would wouldn't you you'd you'll you're you've your yours
yourself yourselves
""".split())

PLATFORM_WORDS = set("""
discord substack github twitter tweet tweets tweeting reddit youtube tiktok
instagram facebook whatsapp telegram signal slack zoom google gmail email emails
dm dms ping pinged server servers channel channels thread threads bot bots online
offline post posts posted posting repost blog blogs blogpost link links website
websites site sites app apps webpage web internet wifi username usernames profile
profiles avatar login notification notifications feed feeds subscribe subscribed
subscriber subscribers follow follows following followers unfollow upvote upvotes
downvote comment comments commented meme memes gif gifs emoji emojis hashtag
stream streaming podcast podcasts spotify patreon paypal venmo crypto bitcoin eth
url urls browser chrome firefox android iphone ios laptop desktop pc keyboard
screenshot screenshots video videos audio mic camera call calls calling voice vc
chat chats chatting message messages messaged messaging group groups admin admins
mod mods moderator lurker lurkers
""".split())

# Words that carry conversation but rarely say what a conversation is about.
# This is deliberately a domain-independent class: these terms remain useful
# for a future voice/style analysis, but they must not become lexical weather
# or topic labels merely because chat uses them more often than edited prose.
DISCOURSE_SCAFFOLDING = set("""
able actual actually agree agreed allow allowed allowing already also answer anyone anything
asked asking aware basically believe better becomes bit build builds bunch claim claimed claims
conclusion confused concept context contexts curious definitely difference done dont
easier else enough essentially even eventually ever everybody everyone exactly
entirely etc exist exists explain explained explaining explicitly feel fine
feels feeling felt get gets getting give gives giving going gone
fit fits fitted genuinely gonna good got guess guy guys happen happened happening
generally idk idea ideas imo interesting isnt kinda kind know knows let like likely literally meant might
llm look looks lot lots made make makes making maybe mean means merely mostly much
necessarily need needed needs nice okay one ones others outcome outcomes
people person perfect powerful pretty probably quite rather really relevant right
said say saying says see seeing seem seemed seems sense seriously setting shit simply someone specifically
something somehow sometime sort still stuff supposed sure suspect take takes taking talk ton
talked talking talks thing things towards
think thinking thinks thats tho though thought thoughts try trying understand understanding wondering
understands useful wanna want wanted wants way ways whatever weird willing worth wrong
particularly hmm toward cant couldnt didnt doesnt shouldnt wasnt werent wouldnt
arent everything havent hasnt hadnt wont now last years whole sounds two different back forth
cool sorry thank thanks otherwise piece
yeah yes yet
""".split())

WORD_REFERENCE_URL = "https://norvig.com/ngrams/count_1w.txt"
BIGRAM_REFERENCE_URL = "https://norvig.com/ngrams/count_2w.txt"

# Once a word is two orders of magnitude more common locally, the exact broad
# corpus ratio no longer tells us anything useful about its importance inside
# the community. Saturating here lets adoption decide among all such words and
# prevents reference-list sparsity from becoming the ranking algorithm.
LEXICON_LIFT_SATURATION = 100.0


def parse_time(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def token_stream(text: str) -> tuple[list[str], set[int]]:
    """Return word surfaces and boundaries that phrases may not cross.

    Word counting can ignore punctuation, but phrase counting cannot: removing
    the period from ``theory. Mind`` must not manufacture ``theory mind``.
    URLs and Discord markup become hard boundaries for the same reason.
    Boundary index ``i`` means tokens ``i`` and ``i + 1`` were not separated
    by whitespace alone.
    """
    clean = URL.sub("\n", DISCORD_MARKUP.sub("\n", text)).lower()
    matches = list(WORD.finditer(clean))
    tokens = [match.group() for match in matches]
    boundaries = {
        index
        for index in range(len(matches) - 1)
        if not clean[matches[index].end():matches[index + 1].start()].isspace()
        or "\n" in clean[matches[index].end():matches[index + 1].start()]
    }
    return tokens, boundaries


def tokens_of(text: str) -> list[str]:
    return token_stream(text)[0]


def candidate_ngram_spans(
    tokens: list[str],
    size: int,
    boundaries: set[int] | None = None,
):
    """Yield ``(start_index, phrase)`` for structurally valid n-grams.

    Keeping the token position lets topic analysis connect a validated phrase
    to nearby subject words.  The phrase itself is still counted literally;
    no stemming or model-generated rewrite is introduced here.
    """
    boundaries = boundaries or set()
    for index in range(len(tokens) - size + 1):
        if any(gap in boundaries for gap in range(index, index + size - 1)):
            continue
        part = tokens[index:index + size]
        if part[0] in STOPWORDS or part[-1] in STOPWORDS:
            continue
        if sum(word not in STOPWORDS for word in part) < 2:
            continue
        if any(word in PLATFORM_WORDS for word in part):
            continue
        yield index, " ".join(part)


def candidate_ngrams(tokens: list[str], size: int):
    """Yield phrase strings when callers do not need token positions."""
    for _index, phrase in candidate_ngram_spans(tokens, size):
        yield phrase


def emoji_characters(text: str):
    return [
        character
        for character in text
        if 0x1F000 <= ord(character) <= 0x1FAFF
        or 0x2600 <= ord(character) <= 0x27BF
        or 0x2B00 <= ord(character) <= 0x2BFF
    ]


def normalized_domain(raw_url: str) -> str | None:
    try:
        domain = (urlparse(raw_url.rstrip(".,;:!?)]}")).hostname or "").lower()
    except ValueError:
        return None
    if domain.startswith("www."):
        domain = domain[4:]
    return {
        "twitter.com": "x.com",
        "mobile.twitter.com": "x.com",
        "youtu.be": "youtube.com",
        "m.youtube.com": "youtube.com",
    }.get(domain, domain) or None


class ReferenceData:
    """General-English word and bigram frequencies."""

    def __init__(self, words: dict[str, int], bigrams: dict[str, int]):
        self.words = words
        self.bigrams = bigrams
        self.word_total = max(1, sum(words.values()))
        self.bigram_total = max(1, sum(bigrams.values()))

    @classmethod
    def load(cls, directory: Path) -> "ReferenceData":
        directory.mkdir(parents=True, exist_ok=True)
        word_path = directory / "count_1w.txt"
        bigram_path = directory / "count_2w.txt"
        cls._download(word_path, WORD_REFERENCE_URL)
        cls._download(bigram_path, BIGRAM_REFERENCE_URL)
        return cls(cls._counts(word_path), cls._counts(bigram_path))

    @staticmethod
    def _download(path: Path, url: str) -> None:
        if path.exists():
            return
        print(f"downloading reference: {path.name}")
        temporary = path.with_suffix(path.suffix + ".part")
        urllib.request.urlretrieve(url, temporary)
        os.replace(temporary, path)

    @staticmethod
    def _counts(path: Path) -> dict[str, int]:
        counts = {}
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    term, raw_count = line.rstrip("\n").split("\t")
                    counts[term] = int(raw_count)
                except ValueError:
                    continue
        return counts

    def word_rate(self, word: str) -> float:
        normalized = word.replace("'", "").replace("-", "")
        return (self.words.get(word) or self.words.get(normalized) or 0) / self.word_total

    def bigram_rate(self, phrase: str) -> float:
        return self.bigrams.get(phrase, 0) / self.bigram_total


class WeatherAnalyzer:
    def __init__(self, days: int, config: dict):
        self.window_days = days
        self.config = config

        # Long-history counters. Movement is a 13-week window compared with
        # the preceding 13 weeks, so it cannot be derived from the weather
        # window alone. Both layers share this tokenizer and phrase inventory.
        self.history_messages = 0
        self.history_tokens = 0
        self.history_weekly_tokens = collections.Counter()
        self.history_words = collections.Counter()
        self.history_word_speakers = collections.defaultdict(set)
        self.history_word_weekly = collections.defaultdict(collections.Counter)
        self.history_word_first: dict[str, datetime] = {}
        self.history_phrases = {2: collections.Counter(), 3: collections.Counter()}
        self.history_phrase_speakers = {
            2: collections.defaultdict(set),
            3: collections.defaultdict(set),
        }
        self.history_phrase_weekly = {
            2: collections.defaultdict(collections.Counter),
            3: collections.defaultdict(collections.Counter),
        }
        self.history_phrase_first = {2: {}, 3: {}}
        self.history_first_time: datetime | None = None
        self.history_last_time: datetime | None = None

        # Current-window counters used by every visible page except movement.
        self.messages = 0
        self.tokens = 0
        self.channels = collections.Counter()
        self.words = collections.Counter()
        self.word_speakers = collections.defaultdict(set)
        self.word_days = collections.defaultdict(collections.Counter)
        self.phrases = {2: collections.Counter(), 3: collections.Counter()}
        self.phrase_speakers = {
            2: collections.defaultdict(set),
            3: collections.defaultdict(set),
        }
        self.phrase_days = {
            2: collections.defaultdict(collections.Counter),
            3: collections.defaultdict(collections.Counter),
        }
        self.daily_messages = collections.Counter()
        self.daily_tokens = collections.Counter()
        self.daily_voices = collections.defaultdict(set)
        self.domains = collections.Counter()
        self.domain_days = collections.defaultdict(collections.Counter)
        self.domain_speakers = collections.defaultdict(set)
        self.symbols = collections.Counter()
        self.heatmap = [[0] * 24 for _ in range(7)]

        # Compact attribution. These support top talkers, conversation circles,
        # channel profiles, and the client-side "subtract a voice" lexicon lens.
        self.people_names: dict[str, str] = {}
        self.people_messages = collections.Counter()
        self.people_tokens = collections.Counter()
        self.people_days = collections.defaultdict(set)
        self.people_channels = collections.defaultdict(collections.Counter)
        self.people_words = collections.defaultdict(collections.Counter)
        self.people_phrases = {
            2: collections.defaultdict(collections.Counter),
            3: collections.defaultdict(collections.Counter),
        }
        self.channel_speakers = collections.defaultdict(set)
        self.channel_words = collections.defaultdict(collections.Counter)
        self.channel_phrases = {
            2: collections.defaultdict(collections.Counter),
            3: collections.defaultdict(collections.Counter),
        }
        # Topic weather is counted in a second, phrase-aware pass. A qualified
        # bigram claims its two token positions, so one occurrence cannot also
        # inflate both component-word nodes. The canonical history is replayed
        # by build.py; token sequences are never retained here.
        self.semantic_ready = False
        self.semantic_bigrams: dict[str, dict] = {}
        self.semantic_counts = collections.Counter()
        self.semantic_kinds: dict[str, str] = {}
        self.semantic_speakers = collections.defaultdict(set)
        self.semantic_days = collections.defaultdict(collections.Counter)
        self.semantic_documents = collections.Counter()
        self.semantic_document_days = collections.defaultdict(collections.Counter)
        self.semantic_pairs = collections.Counter()
        self.semantic_pair_days = collections.defaultdict(collections.Counter)
        self.semantic_people_words = collections.defaultdict(collections.Counter)
        self.semantic_people_phrases = collections.defaultdict(collections.Counter)
        self.semantic_channel_words = collections.defaultdict(collections.Counter)
        self.semantic_channel_phrases = collections.defaultdict(collections.Counter)
        self.member_alias_tokens: set[str] = set()
        self.first_time: datetime | None = None
        self.last_time: datetime | None = None

    def observe(
        self,
        *,
        content: str,
        timestamp: str | None,
        channel: str,
        thread: str | None,
        speaker: str,
        reactions: list[dict],
        attachments: list[dict],
        display_name: str | None = None,
        current: bool = True,
    ) -> None:
        """Reduce one message to counters; content itself is never retained."""
        observed_at = parse_time(timestamp)
        if observed_at is None:
            return
        day = observed_at.date().isoformat()
        week = (observed_at.date() - timedelta(days=observed_at.weekday())).isoformat()
        tokens, phrase_boundaries = token_stream(content)

        self.history_messages += 1
        self.history_tokens += len(tokens)
        self.history_weekly_tokens[week] += len(tokens)
        self.history_first_time = min(self.history_first_time or observed_at, observed_at)
        self.history_last_time = max(self.history_last_time or observed_at, observed_at)
        for word in tokens:
            self.history_words[word] += 1
            self.history_word_speakers[word].add(speaker)
            self.history_word_weekly[word][week] += 1
            self.history_word_first.setdefault(word, observed_at)
        history_phrases = {2: [], 3: []}
        history_phrase_spans = {2: [], 3: []}
        for size in (2, 3):
            history_phrase_spans[size] = list(
                candidate_ngram_spans(tokens, size, phrase_boundaries)
            )
            history_phrases[size] = [phrase for _index, phrase in history_phrase_spans[size]]
            for phrase in history_phrases[size]:
                self.history_phrases[size][phrase] += 1
                self.history_phrase_speakers[size][phrase].add(speaker)
                self.history_phrase_weekly[size][phrase][week] += 1
                self.history_phrase_first[size].setdefault(phrase, observed_at)

        if not current:
            return

        self.messages += 1
        self.tokens += len(tokens)
        self.channels[channel or "?"] += 1
        self.daily_messages[day] += 1
        self.daily_tokens[day] += len(tokens)
        self.daily_voices[day].add(speaker)
        self.heatmap[observed_at.weekday()][observed_at.hour] += 1
        self.first_time = min(self.first_time or observed_at, observed_at)
        self.last_time = max(self.last_time or observed_at, observed_at)
        self.people_names[speaker] = display_name or self.people_names.get(speaker) or "?"
        self.people_messages[speaker] += 1
        self.people_tokens[speaker] += len(tokens)
        self.people_days[speaker].add(day)
        self.people_channels[speaker][channel or "?"] += 1
        self.channel_speakers[channel or "?"].add(speaker)

        for word in tokens:
            self.words[word] += 1
            self.word_speakers[word].add(speaker)
            self.word_days[word][day] += 1
            self.people_words[speaker][word] += 1
            self.channel_words[channel or "?"][word] += 1

        for size in (2, 3):
            for phrase in history_phrases[size]:
                self.phrases[size][phrase] += 1
                self.phrase_speakers[size][phrase].add(speaker)
                self.phrase_days[size][phrase][day] += 1
                self.people_phrases[size][speaker][phrase] += 1
                self.channel_phrases[size][channel or "?"][phrase] += 1

        for raw_url in URL.findall(content):
            domain = normalized_domain(raw_url)
            if domain:
                self.domains[domain] += 1
                self.domain_days[domain][day] += 1
                self.domain_speakers[domain].add(speaker)

        self.symbols.update(CUSTOM_EMOJI.findall(content))
        self.symbols.update(emoji_characters(content))
        for reaction in reactions:
            raw_emoji = reaction.get("emoji") or "?"
            emoji = raw_emoji.get("name") if isinstance(raw_emoji, dict) else raw_emoji
            emoji = emoji or "?"
            self.symbols[emoji] += int(reaction.get("count") or 0)
        if attachments:
            self.symbols["media attachment"] += 1

    def weather_bigram_rows(self, phrases: list[dict]) -> dict[str, dict]:
        """Return constructions broad enough to become semantic weather."""
        minimum_count = int(
            self.config.get("topic_bigram_minimum_count", self.config["minimum_word_count"])
        )
        minimum_speakers = int(
            self.config.get(
                "topic_bigram_minimum_speakers",
                max(3, int(self.config["minimum_speakers"])),
            )
        )
        minimum_days = int(self.config.get("topic_bigram_minimum_days", 2))
        return {
            row["term"]: row
            for row in phrases
            if row["kind"] == "2gram"
            and row["count"] >= minimum_count
            and row["voices"] >= minimum_speakers
            and len(self.phrase_days[2][row["term"]]) >= minimum_days
        }

    def begin_semantic_pass(self, phrases: list[dict]) -> None:
        """Initialize the non-overlapping word/bigram topic inventory.

        Phrase discovery must finish before this pass begins. build.py then
        replays only the current weather window through ``observe_semantic``.
        """
        self.semantic_bigrams = self.weather_bigram_rows(phrases)
        self.semantic_ready = True

    def _semantic_units(self, content: str) -> list[tuple[str, str, int, int]]:
        """Tokenize one message into non-overlapping semantic observations."""
        tokens, boundaries = token_stream(content)
        units = []
        index = 0
        while index < len(tokens):
            phrase = None
            if index + 1 < len(tokens) and index not in boundaries:
                candidate = f"{tokens[index]} {tokens[index + 1]}"
                if candidate in self.semantic_bigrams:
                    phrase = candidate
            if phrase:
                units.append((phrase, "bigram", index, index + 1))
                index += 2
            else:
                units.append((tokens[index], "word", index, index))
                index += 1
        return units

    def observe_semantic(
        self,
        *,
        content: str,
        timestamp: str | None,
        channel: str,
        speaker: str,
    ) -> None:
        """Count one current message after qualified bigrams become atomic."""
        if not self.semantic_ready:
            raise RuntimeError("begin_semantic_pass must run before semantic replay")
        observed_at = parse_time(timestamp)
        if observed_at is None:
            return
        day = observed_at.date().isoformat()
        units = self._semantic_units(content)

        document_terms = set()
        content_units = []
        for term, kind, start, end in units:
            self.semantic_counts[term] += 1
            self.semantic_kinds[term] = kind
            self.semantic_speakers[term].add(speaker)
            self.semantic_days[term][day] += 1
            if kind == "bigram":
                self.semantic_people_phrases[speaker][term] += 1
                self.semantic_channel_phrases[channel or "?"][term] += 1
                document_terms.add(term)
                content_units.append((start, end, term))
            else:
                self.semantic_people_words[speaker][term] += 1
                self.semantic_channel_words[channel or "?"][term] += 1
                if term not in STOPWORDS and term not in PLATFORM_WORDS:
                    document_terms.add(term)
                    content_units.append((start, end, term))
        self.semantic_documents.update(document_terms)
        for term in document_terms:
            self.semantic_document_days[term][day] += 1

        local_pairs = set()
        for left_index, (_left_start, left_end, left) in enumerate(content_units):
            for right_start, _right_end, right in content_units[left_index + 1:]:
                if right_start - left_end > 8:
                    break
                if left != right:
                    local_pairs.add(tuple(sorted((left, right))))
        self.semantic_pairs.update(local_pairs)
        for pair in local_pairs:
            self.semantic_pair_days[pair][day] += 1

    def reportable_word(self, word: str) -> bool:
        return (
            len(word) >= 3
            and word not in STOPWORDS
            and word not in PLATFORM_WORDS
            and word not in self.member_alias_tokens
            and word not in set(self.config.get("exclude_words", []))
            and "'" not in word
            and not word.startswith("http")
        )

    def aboutness_word(
        self,
        word: str,
        reference: ReferenceData,
        lift: float | None = None,
    ) -> bool:
        """Whether a term is specific enough to anchor subject matter.

        General-English rarity alone mistakes typos for topics; local lift
        alone mistakes ordinary chat language for topics. An anchor must pass
        both tests (or be absent from the reference corpus altogether).
        """
        if not self.reportable_word(word) or word in DISCOURSE_SCAFFOLDING:
            return False
        reference_rate = reference.word_rate(word)
        if reference_rate == 0:
            return True
        reference_ppm = reference_rate * 1_000_000
        return (
            reference_ppm <= float(self.config["topic_maximum_reference_ppm"])
            and (lift if lift is not None else self.word_lift(word, reference))
            >= float(self.config["topic_minimum_lift"])
        )

    def topic_canonical(self, word: str) -> str:
        """Fold a conservative plural only when its singular was observed.

        Surface forms remain untouched everywhere else. This exists solely so
        grammatical number cannot create parallel topic fronts.
        """
        candidates = []
        if len(word) > 4 and word.endswith("ies"):
            candidates.append(word[:-3] + "y")
        if len(word) > 4 and word.endswith(("sses", "xes", "zes", "ches", "shes")):
            candidates.append(word[:-2])
        if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
            candidates.append(word[:-1])
        return next((candidate for candidate in candidates if candidate in self.words), word)

    def day_axis(self) -> list[str]:
        end = (self.last_time or datetime.now(timezone.utc)).date()
        start = end - timedelta(days=self.window_days - 1)
        return [(start + timedelta(days=offset)).isoformat() for offset in range(self.window_days)]

    def word_lift(self, word: str, reference: ReferenceData) -> float:
        local_rate = self.words[word] / max(1, self.tokens)
        return local_rate / max(reference.word_rate(word), 1e-8)

    def lexicon(self, reference: ReferenceData) -> list[dict]:
        """Rank characteristic vocabulary by adoption, not extreme ratios.

        A characteristic term needs both local adoption and broad-web
        distinctiveness. Distinctiveness is logarithmic and saturates at two
        orders of magnitude, so a 10,000x lookup does not receive more rank
        credit than 100x. Uses and distinct voices then decide among terms
        beyond that point.
        """
        minimum_count = int(self.config["minimum_word_count"])
        minimum_speakers = int(self.config["minimum_speakers"])
        minimum_lift = float(self.config["word_reference_minimum_lift"])
        rows = []
        for word, count in self.words.items():
            if not self.reportable_word(word) or word in DISCOURSE_SCAFFOLDING:
                continue
            speakers = len(self.word_speakers[word])
            if count < minimum_count or speakers < minimum_speakers:
                continue
            reference_rate = reference.word_rate(word)
            local_rate = count / max(1, self.tokens)
            lift = local_rate / reference_rate if reference_rate else None
            if lift is not None and lift < minimum_lift:
                continue
            adoption = math.log1p(count) * math.log1p(speakers)
            bounded_lift = min(
                lift if lift is not None else LEXICON_LIFT_SATURATION,
                LEXICON_LIFT_SATURATION,
            )
            distinctiveness = math.log2(max(minimum_lift, bounded_lift))
            score = adoption * distinctiveness
            rows.append({
                "term": word,
                "count": count,
                "voices": speakers,
                "reference_status": "not_found" if reference_rate == 0 else "measured",
                "reference_expected": (
                    round(reference_rate * self.tokens, 4)
                    if reference_rate else None
                ),
                "reference_ppm": (
                    round(reference_rate * 1_000_000, 4)
                    if reference_rate else None
                ),
                "local_ppm": round(local_rate * 1_000_000, 1),
                "lift": round(lift, 1) if lift is not None else None,
                "score": score,
            })
        rows.sort(key=lambda row: (-row["score"], -row["count"], row["term"]))
        for row in rows:
            row.pop("score")
        return rows[:80]

    def phrase_rows(self, size: int, reference: ReferenceData) -> list[dict]:
        """Return repeated constructions that are associated and locally useful.

        Bigrams have a broad-English frequency reference, so a common phrase
        must also be unusually frequent here. The bigram rule compares whole
        phrases rather than requiring a rare component word; that is why an
        ordinary-word construction such as ``september event`` survives.

        No comparable trigram corpus is bundled. Trigrams therefore need two
        independently topic-bearing terms, which removes sentence fragments
        without pretending we know their general-English phrase rate.
        """
        minimum_count = int(self.config["minimum_phrase_count"])
        minimum_speakers = int(self.config["minimum_speakers"])
        excluded = set(self.config.get("exclude_phrases", []))
        positions = max(1, self.tokens - (size - 1) * self.messages)
        rows = []

        for phrase, count in self.phrases[size].items():
            parts = phrase.split()
            speakers = len(self.phrase_speakers[size][phrase])
            if (
                count < minimum_count
                or speakers < minimum_speakers
                or phrase in excluded
                or any(not self.reportable_word(word) for word in parts if word not in STOPWORDS)
                or all(
                    word in STOPWORDS or word in DISCOURSE_SCAFFOLDING
                    for word in parts
                )
            ):
                continue

            local_probability = count / positions
            independent_probability = math.prod(
                self.words[word] / max(1, self.tokens) for word in parts
            )
            pmi = math.log2(local_probability / max(independent_probability, 1e-18))
            association = pmi / max(1e-12, -math.log2(local_probability))
            if association <= 0:
                continue

            reference_lift = None
            if size == 2:
                reference_rate = reference.bigram_rate(phrase)
                reference_lift = local_probability / max(reference_rate, 1e-10)
                if (
                    reference_rate > 0
                    and reference_lift
                    < float(self.config.get("bigram_reference_minimum_lift", 10.0))
                ):
                    continue
            elif sum(
                self.aboutness_word(word, reference)
                for word in dict.fromkeys(parts)
                if word not in STOPWORDS
            ) < int(self.config.get("trigram_minimum_aboutness_terms", 2)):
                continue

            score = (
                math.log1p(count)
                * math.log1p(speakers)
                * association
            )
            rows.append({
                "term": phrase,
                "kind": f"{size}gram",
                "count": count,
                "voices": speakers,
                "association": round(association, 3),
                "reference_lift": round(reference_lift, 1) if reference_lift else None,
                "score": score,
            })

        rows.sort(key=lambda row: (-row["score"], -row["count"], row["term"]))
        for row in rows:
            row.pop("score")
        return rows[:80]

    @staticmethod
    def _communities(graph: dict[str, dict[str, float]], resolution: float):
        """One-level weighted modularity clustering (deterministic Louvain)."""
        degree = {node: sum(neighbors.values()) for node, neighbors in graph.items()}
        total_weight_twice = sum(degree.values()) or 1.0
        community = {node: node for node in graph}
        totals = dict(degree)

        for _ in range(30):
            moved = False
            for node in sorted(graph, key=lambda item: (-degree[item], item)):
                old = community[node]
                node_degree = degree[node]
                totals[old] -= node_degree
                weights = collections.Counter()
                for neighbor, weight in graph[node].items():
                    weights[community[neighbor]] += weight

                best = old
                best_gain = 0.0
                for candidate, internal_weight in weights.items():
                    gain = internal_weight - (
                        resolution * node_degree * totals[candidate] / total_weight_twice
                    )
                    if gain > best_gain + 1e-12 or (
                        abs(gain - best_gain) <= 1e-12 and candidate < best
                    ):
                        best = candidate
                        best_gain = gain
                community[node] = best
                totals[best] += node_degree
                moved |= best != old
            if not moved:
                break

        groups = collections.defaultdict(set)
        for node, label in community.items():
            groups[label].add(node)
        return list(groups.values())

    def topics(
        self,
        reference: ReferenceData,
        days: list[str],
    ) -> tuple[list[dict], list[dict]]:
        """Build one semantic graph from qualified words and bigrams.

        Words enter through the broad-English aboutness gate.  Bigrams first
        have to pass ``phrase_rows`` (frequency, voice breadth, association,
        and whole-phrase reference lift), then meet the stricter weather gate:
        five uses, three voices, and two active dates by default.  This makes
        ``september event`` a first-class unit without allowing every repeated
        two-person expression to become atmospheric weather.
        """
        if not self.semantic_ready:
            raise RuntimeError("topic analysis requires the phrase-aware semantic replay")
        minimum_word_count = int(self.config["minimum_word_count"])
        minimum_speakers = int(self.config["minimum_speakers"])
        minimum_pair_count = int(self.config["topic_minimum_pair_count"])
        minimum_association = float(self.config["topic_minimum_association"])

        # Canonicalize conservative plural variants after atomic phrase
        # matching. Phrase surfaces remain literal because their construction
        # is the signal; component occurrences are no longer present here.
        unit_counts = collections.Counter()
        unit_speakers = collections.defaultdict(set)
        unit_days = collections.defaultdict(collections.Counter)
        unit_documents = collections.Counter()
        unit_kind = {}
        for term, count in self.semantic_counts.items():
            kind = self.semantic_kinds[term]
            canonical = term if kind == "bigram" else self.topic_canonical(term)
            unit_counts[canonical] += count
            unit_speakers[canonical].update(self.semantic_speakers[term])
            unit_days[canonical].update(self.semantic_days[term])
            unit_documents[canonical] = min(
                self.messages,
                unit_documents[canonical] + self.semantic_documents[term],
            )
            unit_kind[canonical] = kind

        unit_pairs = collections.Counter()
        unit_pair_days = collections.defaultdict(collections.Counter)
        for (left, right), count in self.semantic_pairs.items():
            canonical_left = (
                left if self.semantic_kinds[left] == "bigram" else self.topic_canonical(left)
            )
            canonical_right = (
                right if self.semantic_kinds[right] == "bigram" else self.topic_canonical(right)
            )
            pair = tuple(sorted((canonical_left, canonical_right)))
            if pair[0] != pair[1]:
                unit_pairs[pair] += count
                unit_pair_days[pair].update(self.semantic_pair_days[(left, right)])

        unit_lifts = {}
        for term, count in unit_counts.items():
            if unit_kind[term] == "bigram":
                unit_lifts[term] = self.semantic_bigrams[term]["reference_lift"] or 1.0
            else:
                unit_lifts[term] = (
                    (count / max(1, self.tokens))
                    / max(reference.word_rate(term), 1e-8)
                )

        word_candidates = []
        for word, count in unit_counts.items():
            if unit_kind[word] != "word":
                continue
            if (
                count < minimum_word_count
                or len(unit_speakers[word]) < minimum_speakers
                or not self.reportable_word(word)
            ):
                continue
            lift = unit_lifts[word]
            if not self.aboutness_word(word, reference, lift):
                continue
            specificity = min(8, max(0, math.log2(max(1, lift))))
            interest = math.log1p(count) * math.log1p(len(unit_speakers[word]))
            interest *= 1 + specificity
            word_candidates.append((interest, word))
        word_candidates.sort(reverse=True)
        nodes = {word for _interest, word in word_candidates[:240]}

        # Qualified bigrams are already atomic observations in this inventory.
        eligible_bigrams = self.semantic_bigrams
        nodes.update(eligible_bigrams)

        # Atomic counting prevents duplication; composition edges answer a
        # different question and organize the graph. They place the compound
        # beside independently used component concepts without pretending the
        # phrase occurrence was also two word occurrences.
        composition_association = {}
        for phrase, row in eligible_bigrams.items():
            for component in dict.fromkeys(phrase.split()):
                word = self.topic_canonical(component)
                if word not in nodes:
                    continue
                pair = tuple(sorted((phrase, word)))
                composition_association[pair] = row["association"]
                unit_pairs[pair] = max(
                    unit_pairs[pair], self.semantic_documents[phrase]
                )
                for day, count in self.semantic_document_days[phrase].items():
                    unit_pair_days[pair][day] = max(
                        unit_pair_days[pair][day], count
                    )

        graph = {term: {} for term in nodes}
        documents = max(1, self.messages)
        edges = []
        for (left, right), count in unit_pairs.items():
            if left not in nodes or right not in nodes or count < minimum_pair_count:
                continue
            if (left, right) in composition_association:
                association = composition_association[(left, right)]
            else:
                joint = count / documents
                independent = (
                    unit_documents[left] / documents
                    * unit_documents[right] / documents
                )
                pmi = math.log2(joint / max(independent, 1e-18))
                association = pmi / max(1e-12, -math.log2(joint))
            if association < minimum_association:
                continue
            weight = association * math.log1p(count)
            edges.append((weight, left, right))

        # Retain each term's strongest relationships. This prevents ubiquitous
        # hub words from welding every subject into one topic.
        strongest = collections.defaultdict(list)
        for weight, left, right in edges:
            strongest[left].append((weight, right))
            strongest[right].append((weight, left))
        retained = set()
        for node, neighbors in strongest.items():
            for weight, neighbor in sorted(neighbors, reverse=True)[:7]:
                retained.add(tuple(sorted((node, neighbor))))
        for weight, left, right in edges:
            if tuple(sorted((left, right))) in retained:
                graph[left][right] = weight
                graph[right][left] = weight
        graph = {node: links for node, links in graph.items() if links}
        for node in graph:
            graph[node] = {
                neighbor: weight
                for neighbor, weight in graph[node].items()
                if neighbor in graph
            }

        groups = self._communities(
            graph, float(self.config.get("topic_resolution", 1.15))
        )
        assigned = set().union(*groups) if groups else set()
        # A qualified bigram already contains an observed semantic relation,
        # so it may stand alone when its surrounding vocabulary is too diffuse
        # to pass the graph edge gate. Singleton words do not get this privilege.
        groups.extend({phrase} for phrase in eligible_bigrams if phrase not in assigned)

        def ranking_score(term: str) -> float:
            specificity = min(8, max(0, math.log2(max(1, unit_lifts.get(term, 1)))))
            degree = sum(graph.get(term, {}).values())
            return (
                math.log1p(unit_counts[term])
                * math.log1p(len(unit_speakers[term]))
                * (1 + specificity)
                * (1 + degree)
            )

        topics = []
        for group in groups:
            if len(group) < 2 and not any(unit_kind.get(term) == "bigram" for term in group):
                continue
            ranked = sorted(
                group,
                key=lambda term: (-ranking_score(term), term),
            )
            phrase_labels = [term for term in ranked if unit_kind[term] == "bigram"]
            label_terms = phrase_labels[:1] or ranked[:3]
            label = " · ".join(label_terms)
            # When a phrase and one of its component concepts land in the same
            # family, the phrase is the more informative visible descriptor.
            # Component evidence remains a separate, non-overlapping particle
            # node and contributes to every total; only the redundant label is
            # suppressed in the cloud.
            nested_components = {
                self.topic_canonical(component)
                for phrase in phrase_labels
                for component in phrase.split()
            }
            voices = set().union(*(unit_speakers[term] for term in group))
            unit_field = [
                {
                    "term": term,
                    "kind": unit_kind[term],
                    "label_visible": not (
                        unit_kind[term] == "word" and term in nested_components
                    ),
                    "count": unit_counts[term],
                    "voices": len(unit_speakers[term]),
                    "lift": round(unit_lifts[term], 1),
                    "series": [
                        round(
                            unit_days[term][day]
                            / max(1, self.daily_tokens[day])
                            * 10_000,
                            3,
                        )
                        for day in days
                    ],
                }
                for term in ranked[:18]
            ]
            series = [
                round(
                    sum(unit_days[term][day] for term in group)
                    / max(1, self.daily_tokens[day])
                    * 10_000,
                    3,
                )
                for day in days
            ]
            topics.append({
                "id": re.sub(r"[^a-z0-9]+", "-", "-".join(label_terms)).strip("-"),
                "label": label,
                "units": unit_field,
                "voices": len(voices),
                "mentions": sum(unit_counts[term] for term in group),
                "series": series,
                "score": sum(unit_counts[term] for term in group) * math.log1p(len(voices)),
                # Internal membership is needed to restrict graph edges to the
                # twelve visible communities, then removed before serialization.
                "_units": group,
            })
        topics.sort(key=lambda topic: (-topic["score"], topic["label"]))
        topics = topics[:12]

        # The browser graph uses the same statistical edges as clustering, not
        # a second visual-only notion of similarity. Keep a small neighborhood
        # around every visible unit so the graph remains legible while weak
        # nodes are not erased merely because a global top-N cut favored hubs.
        visible_terms = {
            unit["term"]
            for topic in topics
            for unit in topic["units"]
        }
        visible_edges = [
            (weight, left, right)
            for weight, left, right in edges
            if left in visible_terms and right in visible_terms
        ]
        visual_neighbors = collections.defaultdict(list)
        for weight, left, right in visible_edges:
            visual_neighbors[left].append((weight, right))
            visual_neighbors[right].append((weight, left))
        visual_pairs = set()
        for term, neighbors in visual_neighbors.items():
            for _weight, neighbor in sorted(neighbors, reverse=True)[:5]:
                visual_pairs.add(tuple(sorted((term, neighbor))))
        selected_edges = [
            (weight, left, right)
            for weight, left, right in visible_edges
            if tuple(sorted((left, right))) in visual_pairs
        ]
        maximum_edge = max(
            (weight for weight, _left, _right in selected_edges), default=1
        ) or 1
        semantic_edges = [
            {
                "source": left,
                "target": right,
                "weight": round(weight / maximum_edge, 4),
                "documents": unit_pairs[(left, right)],
                "relation": (
                    "composition"
                    if (left, right) in composition_association
                    else "observed_context"
                ),
                "series": [
                    round(
                        unit_pair_days[(left, right)][day]
                        / max(1, self.daily_messages[day])
                        * 1_000,
                        3,
                    )
                    for day in days
                ],
            }
            for weight, left, right in sorted(selected_edges, reverse=True)
        ]

        for topic in topics:
            topic.pop("score")
            topic.pop("_units")
        return topics, semantic_edges

    def _movement_signature(self, term: str) -> tuple[str, ...]:
        """Canonical token signature used only to collapse display duplicates."""
        signature = []
        for word in term.split():
            candidates = []
            if len(word) > 4 and word.endswith("ies"):
                candidates.append(word[:-3] + "y")
            if len(word) > 4 and word.endswith(("sses", "xes", "zes", "ches", "shes")):
                candidates.append(word[:-2])
            if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
                candidates.append(word[:-1])
            signature.append(
                next(
                    (candidate for candidate in candidates if candidate in self.history_words),
                    word,
                )
            )
        return tuple(signature)

    def _diverse_movement(self, rows: list[dict], limit: int = 16) -> list[dict]:
        """Keep one strongest view of each lexical event.

        A phrase, its singular/plural variant, and one of its component words
        otherwise occupy several scarce rows while describing the same change.
        Sorting happens first, so the strongest form becomes the representative.
        """
        selected = []
        signatures: list[tuple[str, ...]] = []
        for row in rows:
            signature = self._movement_signature(row["term"])
            handled = False
            for index, existing in enumerate(signatures):
                if signature == existing or (
                    len(signature) == 1 and signature[0] in existing
                ):
                    handled = True
                    break
                if len(existing) == 1 and existing[0] in signature:
                    # Prefer the construction over a tied component word; it
                    # names the event more precisely while preserving rank.
                    selected[index] = row
                    signatures[index] = signature
                    handled = True
                    break
            if handled:
                continue
            selected.append(row)
            signatures.append(signature)
            if len(selected) == limit:
                break
        return selected

    def movement(self) -> dict:
        """Rank language change over recent 13 weeks versus the prior 13.

        The candidate inventory is deliberately broader than topic detection.
        A phrase made of ordinary English words can still be a meaningful local
        event; count, voice breadth, association, and rate change decide here.
        """
        trend_weeks = int(self.config.get("trend_window_weeks", 13))
        if not self.history_first_time or not self.history_last_time:
            return {"window_weeks": trend_weeks, "rising": [], "fading": []}

        start = self.history_first_time.date() - timedelta(
            days=self.history_first_time.weekday()
        )
        end = self.history_last_time.date() - timedelta(
            days=self.history_last_time.weekday()
        )
        weeks = []
        cursor = start
        while cursor <= end:
            weeks.append(cursor.isoformat())
            cursor += timedelta(days=7)
        current_weeks = weeks[-trend_weeks:]
        previous_weeks = weeks[-2 * trend_weeks:-trend_weeks]
        if len(previous_weeks) < trend_weeks:
            return {
                "window_weeks": trend_weeks,
                "status": "needs_more_history",
                "rising": [],
                "fading": [],
            }

        current_tokens = sum(self.history_weekly_tokens[week] for week in current_weeks)
        previous_tokens = sum(self.history_weekly_tokens[week] for week in previous_weeks)
        minimum_count = int(self.config["minimum_phrase_count"])
        minimum_speakers = int(self.config["minimum_speakers"])
        candidates = []

        def add_candidate(term, kind, count, speakers, weekly, first, association=None):
            current = sum(weekly[week] for week in current_weeks)
            previous = sum(weekly[week] for week in previous_weeks)
            current_rate = current / max(1, current_tokens) * 10_000
            previous_rate = previous / max(1, previous_tokens) * 10_000
            if previous == 0 and current > 0:
                change_label = "new"
                change_percent = None
            elif current == 0 and previous > 0:
                change_label = "not seen recently"
                change_percent = -100
            else:
                ratio = current_rate / max(previous_rate, 1e-12)
                change_percent = round((ratio - 1) * 100)
                change_label = (
                    f"{change_percent}% higher"
                    if change_percent >= 0
                    else f"{abs(change_percent)}% lower"
                )

            # The log-ratio is useful internally because doubling and halving
            # receive equal, opposite ranks. It is deliberately not published:
            # the report shows the plain percent/new/absent label above.
            momentum = math.log2(
                ((current + 0.5) / (current_tokens + 1_000))
                / ((previous + 0.5) / (previous_tokens + 1_000))
            )
            candidates.append({
                "term": term,
                "kind": kind,
                "uses": count,
                "voices": speakers,
                "recent_uses": current,
                "previous_uses": previous,
                "recent_rate": round(current_rate, 3),
                "previous_rate": round(previous_rate, 3),
                "change": change_label,
                "change_percent": change_percent,
                "_momentum": momentum,
                "association": round(association, 3) if association is not None else None,
                "first_observed": first.date().isoformat(),
            })

        for word, count in self.history_words.items():
            speakers = len(self.history_word_speakers[word])
            if (
                count < int(self.config["minimum_word_count"])
                or speakers < minimum_speakers
                or not self.reportable_word(word)
                or word in DISCOURSE_SCAFFOLDING
            ):
                continue
            add_candidate(
                word,
                "word",
                count,
                speakers,
                self.history_word_weekly[word],
                self.history_word_first[word],
            )

        for size in (2, 3):
            positions = max(1, self.history_tokens - (size - 1) * self.history_messages)
            for phrase, count in self.history_phrases[size].items():
                speakers = len(self.history_phrase_speakers[size][phrase])
                parts = phrase.split()
                if (
                    count < minimum_count
                    or speakers < minimum_speakers
                    or phrase in set(self.config.get("exclude_phrases", []))
                    or any(
                        not self.reportable_word(word)
                        for word in parts
                        if word not in STOPWORDS
                    )
                    or all(
                        word in STOPWORDS or word in DISCOURSE_SCAFFOLDING
                        for word in parts
                    )
                ):
                    continue
                probability = count / positions
                independent = math.prod(
                    self.history_words[word] / max(1, self.history_tokens)
                    for word in parts
                )
                pmi = math.log2(probability / max(independent, 1e-18))
                association = pmi / max(1e-12, -math.log2(probability))
                if association <= 0:
                    continue
                add_candidate(
                    phrase,
                    f"{size}gram",
                    count,
                    speakers,
                    self.history_phrase_weekly[size][phrase],
                    self.history_phrase_first[size][phrase],
                    association,
                )

        rising = self._diverse_movement(sorted(
            (
                row
                for row in candidates
                if row["recent_rate"] >= 0.3 and row["_momentum"] > 0.35
            ),
            key=lambda row: (-row["_momentum"], -row["recent_rate"], row["term"]),
        ))
        fading = self._diverse_movement(sorted(
            (
                row
                for row in candidates
                if row["previous_rate"] >= 0.3 and row["_momentum"] < -0.35
            ),
            key=lambda row: (row["_momentum"], -row["previous_rate"], row["term"]),
        ))
        for row in rising + fading:
            row.pop("_momentum")
        return {
            "status": "ready",
            "window_weeks": trend_weeks,
            "previous": {"from": previous_weeks[0], "to": previous_weeks[-1]},
            "recent": {"from": current_weeks[0], "to": current_weeks[-1]},
            "rising": rising,
            "fading": fading,
        }

    @staticmethod
    def _public_person_key(speaker: str) -> str:
        return hashlib.blake2b(speaker.encode("utf-8"), digest_size=6).hexdigest()

    def _topic_counts(
        self,
        word_counts: collections.Counter,
        phrase_counts: collections.Counter,
        topics: list[dict],
    ):
        """Describe a subset's topic mix and its lift over the whole server.

        Topic units can be canonical words or literal bigrams.  Canonicalizing
        the subset's words here also fixes an older undercount where a person's
        plural form did not contribute to a singular topic node.
        """
        canonical_words = collections.Counter()
        for word, count in word_counts.items():
            canonical_words[self.topic_canonical(word)] += count

        def local_count(unit: dict) -> int:
            counter = phrase_counts if unit["kind"] == "bigram" else canonical_words
            return counter[unit["term"]]

        rows = []
        for topic in topics:
            count = sum(local_count(unit) for unit in topic["units"])
            if count:
                global_count = sum(unit["count"] for unit in topic["units"])
                rows.append({
                    "id": topic["id"],
                    "label": topic["label"],
                    "mentions": count,
                    "_global_mentions": global_count,
                })
        rows.sort(key=lambda row: (-row["mentions"], row["label"]))
        total = sum(row["mentions"] for row in rows)
        global_total = sum(row["_global_mentions"] for row in rows)
        for row in rows:
            row["share"] = round(row["mentions"] / max(1, total), 3)
            global_share = row["_global_mentions"] / max(1, global_total)
            row["lift"] = round(
                (row["mentions"] / max(1, total)) / max(global_share, 1e-9),
                2,
            )
            row.pop("_global_mentions")
        return rows

    def people(self, topics: list[dict], lexicon: list[dict], phrases: list[dict]):
        """Serialize named participation and compact lexical contributions."""
        visible_words = {row["term"] for row in lexicon}
        visible_phrases = {row["term"] for row in phrases}
        rows = []
        for speaker, messages in self.people_messages.items():
            words = {
                word: count
                for word, count in self.people_words[speaker].items()
                if word in visible_words
            }
            phrase_counts = self.people_phrases[2][speaker] + self.people_phrases[3][speaker]
            rows.append({
                "key": self._public_person_key(speaker),
                "name": self.people_names.get(speaker) or "?",
                "messages": messages,
                "share": round(messages / max(1, self.messages), 4),
                "active_days": len(self.people_days[speaker]),
                "channels": [
                    {"name": channel, "messages": count}
                    for channel, count in self.people_channels[speaker].most_common(5)
                ],
                "topics": self._topic_counts(
                    self.semantic_people_words[speaker],
                    self.semantic_people_phrases[speaker],
                    topics,
                )[:6],
                "contributions": {
                    "tokens": self.people_tokens[speaker],
                    "words": words,
                    "phrases": {
                        phrase: count
                        for phrase, count in phrase_counts.items()
                        if phrase in visible_phrases
                    },
                },
            })
        rows.sort(key=lambda row: (-row["messages"], row["name"].lower()))
        return rows

    def _distinctive_words(
        self,
        speakers: set[str],
        allowed_words: set[str],
        limit: int = 8,
    ):
        """Find circle-specific terms from the already-vetted language pool."""
        group_words = collections.Counter()
        group_voices = collections.defaultdict(set)
        group_tokens = 0
        for speaker in speakers:
            group_words.update(self.people_words[speaker])
            group_tokens += self.people_tokens[speaker]
            for word, count in self.people_words[speaker].items():
                if count:
                    group_voices[word].add(speaker)
        rest_tokens = max(0, self.tokens - group_tokens)
        rows = []
        for word, count in group_words.items():
            voices = len(group_voices[word])
            if (
                count < 3
                or voices < min(2, len(speakers))
                or word not in allowed_words
                or not self.reportable_word(word)
                or word in DISCOURSE_SCAFFOLDING
            ):
                continue
            rest = self.words[word] - count
            local_rate = (count + 0.5) / (group_tokens + 1_000)
            rest_rate = (rest + 0.5) / (rest_tokens + 1_000)
            lift = local_rate / rest_rate
            score = math.log1p(count) * math.log1p(voices) * math.log2(lift + 1)
            rows.append({
                "term": word,
                "uses": count,
                "voices": voices,
                "lift": round(lift, 1),
                "score": score,
            })
        rows.sort(key=lambda row: (-row["score"], -row["uses"], row["term"]))
        for row in rows:
            row.pop("score")
        return rows[:limit]

    def _distinctive_channel_words(
        self,
        channel: str,
        allowed_words: set[str],
        limit: int = 8,
    ):
        """Find channel-specific terms from the already-vetted language pool."""
        channel_tokens = sum(self.channel_words[channel].values())
        rest_tokens = max(0, self.tokens - channel_tokens)
        rows = []
        for word, count in self.channel_words[channel].items():
            if (
                count < 3
                or word not in allowed_words
                or not self.reportable_word(word)
                or word in DISCOURSE_SCAFFOLDING
            ):
                continue
            rest = self.words[word] - count
            local_rate = (count + 0.5) / (channel_tokens + 1_000)
            rest_rate = (rest + 0.5) / (rest_tokens + 1_000)
            lift = local_rate / rest_rate
            score = math.log1p(count) * math.log2(lift + 1)
            rows.append({
                "term": word,
                "uses": count,
                "lift": round(lift, 1),
                "score": score,
            })
        rows.sort(key=lambda row: (-row["score"], -row["uses"], row["term"]))
        for row in rows:
            row.pop("score")
        return rows[:limit]

    def conversation_circles(self, topics: list[dict], lexicon: list[dict]):
        """Find participant groups from overlapping channel participation.

        The graph is bipartite in origin (people × channels). Two people are
        connected when they repeatedly inhabit the same channels; broad rooms
        are down-weighted so #general does not erase smaller conversational
        neighborhoods. No semantic feature is used to create the groups—topics
        label a circle only after membership has been detected.
        """
        eligible = {
            speaker for speaker, count in self.people_messages.items() if count >= 3
        }
        graph = {speaker: {} for speaker in eligible}
        channel_members = collections.defaultdict(dict)
        for speaker in eligible:
            for channel, count in self.people_channels[speaker].items():
                channel_members[channel][speaker] = count
        weights = collections.Counter()
        for members in channel_members.values():
            if len(members) < 2:
                continue
            breadth_penalty = math.log2(2 + len(members))
            for left, right in itertools.combinations(sorted(members), 2):
                weights[(left, right)] += (
                    math.sqrt(members[left] * members[right]) / breadth_penalty
                )
        neighbors = collections.defaultdict(list)
        for (left, right), weight in weights.items():
            neighbors[left].append((weight, right))
            neighbors[right].append((weight, left))
        retained = set()
        for speaker, links in neighbors.items():
            for _weight, other in sorted(links, reverse=True)[:8]:
                retained.add(tuple(sorted((speaker, other))))
        for (left, right), weight in weights.items():
            if (left, right) not in retained:
                continue
            graph[left][right] = weight
            graph[right][left] = weight
        graph = {speaker: links for speaker, links in graph.items() if links}
        for speaker in graph:
            graph[speaker] = {
                other: weight for other, weight in graph[speaker].items() if other in graph
            }
        groups = self._communities(
            graph, float(self.config.get("conversation_resolution", 1.25))
        )
        groups = [group for group in groups if len(group) >= 2]
        groups.sort(
            key=lambda group: -sum(self.people_messages[speaker] for speaker in group)
        )

        circles = []
        # A circle should not reintroduce terms rejected by the global quality
        # gates. It may only re-rank valid lexicon or topic terms by local lift.
        allowed_words = {row["term"] for row in lexicon}
        allowed_words.update(
            unit["term"]
            for topic in topics
            for unit in topic["units"]
            if unit["kind"] == "word"
        )
        for index, group in enumerate(groups[:6], 1):
            messages = sum(self.people_messages[speaker] for speaker in group)
            channel_counts = collections.Counter()
            word_counts = collections.Counter()
            phrase_counts = collections.Counter()
            for speaker in group:
                channel_counts.update(self.people_channels[speaker])
                word_counts.update(self.semantic_people_words[speaker])
                phrase_counts.update(self.semantic_people_phrases[speaker])
            ranked_channels = sorted(
                channel_counts.items(),
                key=lambda item: (
                    -(item[1] * item[1] / max(1, self.channels[item[0]])),
                    item[0],
                ),
            )[:5]
            topic_rows = self._topic_counts(word_counts, phrase_counts, topics)[:5]
            channel_label = ranked_channels[0][0] if ranked_channels else "shared rooms"
            characteristic_topic = max(
                topic_rows,
                key=lambda row: (
                    math.log1p(row["mentions"])
                    * (1 + max(0, math.log2(max(row["lift"], 1e-9)))),
                    row["label"],
                ),
                default=None,
            )
            topic_label = (
                characteristic_topic["label"].split(" · ")[0]
                if characteristic_topic else "conversation"
            )
            circles.append({
                "id": f"circle-{index}",
                "label": f"#{channel_label} · {topic_label}",
                "messages": messages,
                "share": round(messages / max(1, self.messages), 3),
                "members": [
                    {
                        "key": self._public_person_key(speaker),
                        "name": self.people_names.get(speaker) or "?",
                        "messages": self.people_messages[speaker],
                    }
                    for speaker in sorted(
                        group,
                        key=lambda item: (-self.people_messages[item], self.people_names.get(item, "")),
                    )
                ],
                "channels": [
                    {"name": channel, "messages": count}
                    for channel, count in ranked_channels
                ],
                "topics": topic_rows,
                "language": self._distinctive_words(set(group), allowed_words),
            })

        channel_profiles = []
        for channel, messages in self.channels.most_common(10):
            speakers = set(self.channel_speakers[channel])
            topic_rows = self._topic_counts(
                self.semantic_channel_words[channel],
                self.semantic_channel_phrases[channel],
                topics,
            )[:4]
            channel_profiles.append({
                "name": channel,
                "messages": messages,
                "voices": len(speakers),
                "people": [
                    {
                        "key": self._public_person_key(speaker),
                        "name": self.people_names.get(speaker) or "?",
                        "messages": self.people_channels[speaker][channel],
                    }
                    for speaker in sorted(
                        speakers,
                        key=lambda item: (
                            -self.people_channels[item][channel],
                            self.people_names.get(item, ""),
                        ),
                    )[:6]
                ],
                "topics": topic_rows,
                "language": self._distinctive_channel_words(channel, allowed_words),
            })
        return circles, channel_profiles

    def finalize(
        self,
        reference: ReferenceData,
        *,
        server: dict,
        coverage: dict,
        member_alias_tokens: set[str],
    ) -> dict:
        self.member_alias_tokens = member_alias_tokens
        days = self.day_axis()
        lexicon = self.lexicon(reference)
        bigrams = self.phrase_rows(2, reference)
        trigrams = self.phrase_rows(3, reference)
        topics, semantic_edges = self.topics(reference, days)
        movement = self.movement()
        people = self.people(topics, lexicon, bigrams + trigrams)
        circles, channel_profiles = self.conversation_circles(topics, lexicon)

        return {
            "schema": "ori-community-weather-v6",
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "server": server,
            "window": {
                "days": self.window_days,
                "from": days[0],
                "to": days[-1],
            },
            "privacy": {
                "level": "attributed_community_aggregate",
                "raw_messages_stored": False,
                "author_names_stored": True,
                "user_ids_stored": False,
                "message_ids_stored": False,
                "message_urls_stored": False,
                "member_roster_stored": False,
                "attribution": "display names with compact per-person aggregate counts",
            },
            "stats": {
                "messages": self.messages,
                "tokens": self.tokens,
                "active_voices": len(set().union(*self.daily_voices.values()))
                if self.daily_voices else 0,
                "server_members": coverage.get("member_count"),
                "channels_observed": len(self.channels),
                "history_messages": self.history_messages,
                "history_from": (
                    self.history_first_time.date().isoformat()
                    if self.history_first_time else None
                ),
                "history_to": (
                    self.history_last_time.date().isoformat()
                    if self.history_last_time else None
                ),
            },
            "days": days,
            "activity": {
                "messages": [self.daily_messages[day] for day in days],
                "tokens": [self.daily_tokens[day] for day in days],
                "voices": [len(self.daily_voices[day]) for day in days],
                "heatmap": self.heatmap,
                "timezone": "UTC",
            },
            "lexicon": {
                "reference": "Norvig count_1w / Google Web Trillion Word Corpus",
                "characteristic": lexicon[:50],
            },
            "phrases": {
                "measure": "observed frequency + voice breadth + normalized PMI",
                "bigrams": bigrams[:30],
                "trigrams": trigrams[:30],
            },
            "movement": movement,
            "topics": topics,
            "semantic_graph": {
                "edge_method": (
                    "normalized association from composition or repeated observation "
                    "within an eight-token message neighborhood"
                ),
                "edge_series_unit": "co-occurring message documents per 1k daily messages",
                "edges": semantic_edges,
            },
            "sources": [
                {
                    "domain": domain,
                    "count": count,
                    "voices": len(self.domain_speakers[domain]),
                    "series": [self.domain_days[domain][day] for day in days],
                }
                for domain, count in self.domains.most_common(20)
            ],
            "symbols": [
                {"symbol": symbol, "count": count}
                for symbol, count in self.symbols.most_common(20)
            ],
            "people": people,
            "conversation_circles": circles,
            "channel_profiles": channel_profiles,
            "coverage": coverage,
            "method": {
                "configuration": self.config,
                "lexicon_lift_saturation": LEXICON_LIFT_SATURATION,
                "tokenization": "lowercase word surfaces; phrases cannot cross punctuation, URLs, Discord markup, or newlines",
                "lexicon": "three-times-broad-web eligibility gate; ranked by observed adoption times logarithmic distinctiveness capped at 100x; broad-web comparison shown as expected uses in an equal-size sample",
                "phrases": "literal adjacent bigrams/trigrams; frequency, voice breadth, normalized PMI, and whole-bigram reference lift",
                "movement": "plain normalized percentage change across recent 13 weeks versus the preceding 13; smoothed log-ratio used only for ranking",
                "topic_method": "phrase-aware atomic replay; aboutness-gated words and qualified bigrams in one composition/context graph; weighted modularity",
                "circles": "weighted participant overlap across origin channels",
                "topic_series_unit": "member-filtered non-overlapping semantic-unit mentions per 10k words",
                "external_baseline": "not connected yet",
            },
        }
