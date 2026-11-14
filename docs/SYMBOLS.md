# Реестр символов и публичных API (BormoChecker)

Актуальный индекс модулей, констант, классов и функций `bormochecker/` с номерами строк.

## bormochecker/agreement.py

```python
8:def cohen_kappa(...)
```

## bormochecker/bench.py

```python
23:REPO_ROOT
25:BENCHMARK_DATETIME
26:NOW
27:EXAMPLES
29:NOT_ERROR
32:CASES
67:def score(...)
83:def _parse_ref_dt(...)
92:def main(...)
```

## bormochecker/check_json.py

```python
14:def main(...)
```

## bormochecker/contract.py

```python
12:MAX_TEXT_CHARS
15:class CheckMode:
21:class Label:
29:class Source:
42:class Claim:
67:class Costs:
94:class CheckRequest:
117:class Report:
```

## bormochecker/costs.py

```python
15:def _price(...)
20:def _add(...)
25:def _mul(...)
31:def _per_m(...)
36:def _sum(...)
40:PAID_SEARCH_PROVIDERS
43:class Meter:
```

## bormochecker/crypto.py

```python
14:def _b64u(...)
18:def _unb64u(...)
22:def sign(...)
29:def unsign(...)
42:class RateLimiter:
```

## bormochecker/deadline.py

```python
7:class Deadline:
```

## bormochecker/derived.py

```python
27:OPERATIONS
28:SCOPE_KEYS
31:def _decimal(...)
35:def _fold(...)
40:def _display(...)
45:def _scope_is_verified(...)
68:def _tolerance(...)
80:class Derived:
88:def number_occurs(...)
104:def evaluate(...)
169:_MONTHS_RU
173:_WEEKDAYS_RU
177:_WEEKDAY_NAMES_NOM
180:def check_weekday_dates(...)
307:def _has_noun(...)
321:def check_curriculum_hours_sum(...)
350:def load_education_norms(...)
361:def check_known_education_orders(...)
480:def check_percentage_arithmetic(...)
580:def check_average_ratio_arithmetic(...)
645:def check_mean_as_range_error(...)
746:_SCALAR_STAT_HEAD_RE
752:_VARIATION_EXEMPTION_RE
760:def check_scalar_statistic_as_range(...)
813:def _scan_delta_and_parts(...)
893:def check_delta_and_parts_consistency(...)
902:def check_presupposition_shift(...)
956:def audit_derived_claims(...)
```

## bormochecker/direct_apis.py

```python
35:UA
37:_NORMATIVE_RE
42:_SCIENCE_RE
47:_MOSCOW_RE
53:def is_blocked_domain(...)
58:class DirectDocument:
65:class RuWikipediaAPI:
138:class PravoGovAPI:
189:class CyberLeninkaAPI:
232:class MosRuSuggestAPI:
271:class FgosReestrAPI:
306:def _is_direct_api_enabled(...)
310:class DirectAPIs:
```

## bormochecker/eval_manifest.py

```python
19:def compute_file_sha256(...)
30:def compute_dir_sha256_map(...)
44:def get_git_info(...)
64:def get_key_package_versions(...)
77:class RunManifest:
99:def create_run_directory(...)
118:def build_manifest(...)
176:def _json_default(...)
188:def save_run_artifacts(...)
226:class LogTee:
```

## bormochecker/evaluate.py

```python
35:VERDICTS
36:EVIDENCE_REQUIRED
37:RELATIONS
38:MAX_CANDIDATES
39:MOSCOW_TZ
40:SHORT_FRAGMENT
41:CONTEXT_LINES
42:OTHER_NUMBER_BONUS
43:SAME_ORDER_BONUS
44:TEMPORAL_KEYWORD_BONUS
47:_DATE_RE
50:PROMPT
98:CONFIRMATION_CHECK_PROMPT
116:CONFIRMATION_SCHEMA
142:CONTRADICTION_CHECK_V2
156:CONTRADICTION_SCHEMA_V2
184:_SENTENCE_ABBREVS
188:_INITIALS_RE
189:_FRACTION_PERIOD_RE
190:_URL_SENTENCE_RE
193:def split_sentence_spans(...)
243:def split_sentences(...)
248:class _SentenceMatch:
264:class _SentenceSplitter:
270:_SENTENCE_RE
271:_WORD_RE
274:_FALLBACK_LEMMA_MAP
294:_RU_ENDINGS_RE
302:def _lemma(...)
317:def _normalize_temporal_and_numeric(...)
338:def _extract_cardinal_numbers(...)
355:def _stems(...)
375:def _resolve_source_quote(...)
405:def _annotate_quote_with_page(...)
424:def _dates(...)
428:def _extract_temporal_markers(...)
466:class GuardResult:
480:_NEGATION_RE
486:def _has_negation(...)
490:_BENIGN_NEG_PHRASES_RE
496:_VERB_ENDINGS_RE
503:def _extract_verb_polarities(...)
534:def has_predicate_negation_conflict(...)
546:def _verify_confirmation(...)
581:_SUBSET_LIMITER_RE
591:def _verify_contradiction(...)
637:_SUBSET_OR_STAGE_MODIFIERS
649:_PLAN_MODALITY_MARKERS
664:_COMPLETED_PAST_VERBS
679:_NOVELTY_CLAIM_RE
685:_RELATIVE_YEAR_CLAIM_RE
691:def verify_confirmation_cpu(...)
793:def verify_contradiction_cpu(...)
952:def _spans(...)
956:def _expand_table_lines(...)
983:def find_candidates(...)
1113:def _build_eval_schema(...)
1151:EVALUATION_SCHEMA
1154:def _ask(...)
1184:_IDENTIFIER_BEFORE_RE
1190:_IDENTIFIER_AFTER_RE
1195:_MONEY_RANGE_RE
1201:def _extract_money_ranges(...)
1213:def _extract_floor_numbers(...)
1221:def _dim_covered_in_quants(...)
1235:def _has_hard_dimensional_conflict(...)
1274:def _is_identifier_quantity(...)
1294:_PERCENT_FILLER_WORDS
1303:def _is_incompatible_duration_units(...)
1319:def _doc_identity_key(...)
1331:def _percent_clause_lemmas(...)
1351:def _is_compatible_conflict_pair(...)
1367:def _detect_official_numeric_conflict(...)
1481:_UNMODERATED_UGC_PATTERNS
1493:def _is_unmoderated_ugc(...)
1503:def build_editorial_note_suggestion(...)
1511:def _find_conflicting_quantity_pair(...)
1544:def _has_plan_modality_mismatch(...)
1553:def _try_recover_confirmed_candidate(...)
1603:def evaluate_claim(...)
```

## bormochecker/extract.py

```python
28:def _load_opinion_markers(...)
41:GAP_MIN_ALNUM
42:URL_RE
43:NAMED_SOURCE_RE
48:TYPES
49:KINDS
50:IMPORTANCE
52:EXTRACT_JSON_SCHEMA
78:_NORMATIVE_RE
82:_RULE_ACTION_RE
88:def is_normative_rule(...)
99:PROMPT
127:MISSED
133:def _ask(...)
169:def _span(...)
200:def _parse(...)
280:_CONTRAST_CLAUSE_SPLIT_RE
286:def _split_contrasting_floor_clauses(...)
318:_DISCOURSE_INTRO_RE
325:_ENUM_ITEM_RE
331:def consolidate_enumerated_list_claims(...)
402:def gaps(...)
414:def _fallback_extract(...)
451:def extract_claims(...)
630:def absorb_redundant_subspans(...)
739:def one_origin(...)
```

## bormochecker/limits.py

```python
14:def _env(...)
19:class Settings:
```

## bormochecker/llm.py

```python
26:class LLMError:
30:def parse_llm_json(...)
80:def _resolve_endpoint(...)
116:class LLM:
306:def chat(...)
336:def check_health(...)
```

## bormochecker/local_index.py

```python
16:class IndexOutdatedError:
21:def validate_index_version(...)
40:_STOPWORDS
46:_EDU_NORMATIVE_RE
55:_SCHOOL_FRP_RE
62:_UNIVERSITY_ADMISSION_RE
68:_INTERNAL_TOOL_RE
74:def _fts_lemma(...)
78:def is_education_or_normative_query(...)
85:def build_fts_match_query(...)
106:class LocalDocHit:
113:class LocalKnowledgeIndex:
```

## bormochecker/log_masking.py

```python
10:_SECRET_PATTERNS
32:_ENV_SECRET_NAMES
45:def mask_sensitive_data(...)
63:class SecretMaskingFilter:
78:def install_secret_masker(...)
```

## bormochecker/numerals.py

```python
18:CARDINALS
68:ORDINALS
103:def parse_number(...)
159:def parse_cardinal(...)
169:def parse_ordinal(...)
179:def words_to_num(...)
184:def are_numbers_equal(...)
223:_NUMERAL_ROOTS
248:_MONTH_STEMS
256:class Quantity:
276:def resolve_target_years(...)
296:def extract_quantities(...)
421:def is_typo_or_minor_numeric_diff(...)
```

## bormochecker/origin.py

```python
19:STATED
20:UNCLEAR
21:ORIGIN_TEXT
24:STATED_MARKERS
31:UNCLEAR_MARKERS
37:NOTE_MARKERS
44:_EXTERNAL_ATTRIBUTION
49:_FINITE_PREDICATE_RE
65:def _contains_verb_or_number(...)
78:def is_heading_without_claim(...)
122:def note_declares_editorial(...)
127:_DEONTIC_PREDICATES_RE
132:_PROBABILISTIC_SAFETY_RE
137:_CYCLIC_PROCESS_RE
145:def is_probabilistic_safety_opinion(...)
151:def is_generic_or_deontic_statement(...)
161:def from_text(...)
173:def from_note(...)
182:def _paragraph_bounds(...)
189:def propagate(...)
218:_OBSERVATION
225:_EXTERNAL_CONTEXT
232:def is_episodic_observation(...)
240:def _looks_like_observation(...)
245:def label_for(...)
264:def explanation_for(...)
```

## bormochecker/paths.py

```python
5:def repo_root(...)
21:def mirrors_dir(...)
28:def knowledge_fts_path(...)
35:def mirrors_fts_path(...)
42:def search_cache_path(...)
49:def fact_ledger_path(...)
56:def official_domains_path(...)
63:def norms_path(...)
70:def opinion_markers_path(...)
77:def aggregator_patterns_path(...)
84:def gpu_lock_path(...)
91:def eval_dir(...)
98:def data_dir(...)
```

## bormochecker/pipeline.py

```python
42:IMPORTANCE_ORDER
43:VERDICT_LABEL
47:SEVERITY
48:LABEL_RU
50:EDITORIAL_TEXT
58:def _env_number(...)
62:def _preload_fact_ledger(...)
97:_NEGATION_RE
100:def _has_negation(...)
104:def quote_matches_claim(...)
121:def _find_attachment_body_quote(...)
147:def _judge(...)
321:def _merge(...)
408:def _summary(...)
417:def _can_use_asyncio(...)
428:def _run_coroutine_sync(...)
441:async def _judge_atoms_concurrent(...)
522:def _emit_metrics_log(...)
554:def check(...)
900:def verify_digest_blocks(...)
```

## bormochecker/retrieval.py

```python
31:BLOCKED_DOMAINS
39:BLOCKED_CIDRS
40:BLOCKED_IPS
43:def _blocked_domains(...)
52:def _blocked_networks(...)
58:def _blocked_ips(...)
64:def _is_explicitly_blocked_ip(...)
68:def _normalize_domain(...)
77:def _extract_host(...)
92:def _matches_blocked_domain(...)
100:def is_blocked_domain_host(...)
108:def _parse_any_ip(...)
162:def is_blocked_host(...)
200:class UnsafeUrlError:
204:class FetchError:
208:class DecompressionBombError:
212:class NetworkNotAllowedError:
220:class FetchedDocument:
230:def normalize_http_url(...)
241:def _allowed_networks(...)
250:_URL_NOISE_TOKENS
254:def load_aggregator_patterns(...)
272:def is_aggregator_or_search_url(...)
290:def is_noise_url(...)
300:def assert_safe_http_url(...)
336:_BLOCK_TAGS
337:_SKIP_TAGS
338:_NOISE_TAGS
339:_VOID_TAGS
340:_NOISE_ATTR_TOKENS
343:class _TextExtractor:
497:def _collapse(...)
502:def extract_title_and_text(...)
509:def _parse_pdf_bytes(...)
606:MAX_DOCX_UNCOMPRESSED_BYTES
607:MAX_DOCX_COMPRESSION_RATIO
608:MAX_DOCX_ARCHIVE_FILES
611:def _validate_docx_archive(...)
639:def _parse_docx_bytes(...)
729:_META_CHARSET_RE
732:class SafePinnedBackend:
804:class SafePinnedTransport:
819:class _NoRedirect:
825:class SafeDocumentFetcher:
```

## bormochecker/samples.py

```python
5:SAMPLES
```

## bormochecker/scripts/build_mirrors_fts.py

```python
26:BASE_DIR
27:DEFAULT_MIRROR_DEST
28:DEFAULT_DB_PATH
30:_TITLE_RE
31:_TAG_RE
34:def extract_html_content(...)
56:def extract_pdf_content(...)
127:def init_db(...)
173:def build_mirrors_index(...)
283:def main(...)
```

## bormochecker/scripts/eval_factcheck_benchmark.py

```python
28:_BOOT_ROOT
44:REPO_ROOT
47:def resolve_eval_dir(...)
57:def validate_eval_dir(...)
83:class ItemResult:
110:def load_benchmark_items(...)
196:DEFAULT_OVERLAP_THRESHOLD
199:def score_item(...)
345:MIET_CHECKS
354:def run_reference_cases(...)
442:def load_dod_criteria(...)
472:def compute_duration_metrics(...)
494:class DiscrepancyCheck:
504:def _extract_labels_for_fragment(...)
542:def _is_strictly_confirmed(...)
557:def _format_labels(...)
563:def evaluate_key_discrepancies(...)
705:def generate_markdown_report(...)
1019:def main(...)
```

## bormochecker/scripts/update_mirrors_index.py

```python
30:def get_doc_count(...)
52:def verify_fts_query(...)
73:def safe_update_index(...)
135:def main(...)
```

## bormochecker/search.py

```python
27:def is_blocked_search_result(...)
31:class SearchError:
36:class SearchResult:
44:_MULTI_SITE_RE
47:def clean_query_for_free_search(...)
56:class YandexSearch:
111:class SearXNGSearch:
182:class DDGSSearch:
265:class CloudFreeRotator:
454:class FreeRuNetSearch:
```

## bormochecker/search_cache.py

```python
19:_STRIP_QUERY_PARAMS
24:DEFAULT_CACHE_PATH
27:def canonical_url(...)
53:def normalize_query_key(...)
60:class SearchCache:
318:def main(...)
```

## bormochecker/sources.py

```python
41:DEFAULT_OFFICIAL_DOMAINS
66:def load_official_domains(...)
76:OFFICIAL_DOMAINS
77:MEDIA_DOMAINS
80:_UNSCRAPEABLE_DOMAINS
83:def _select_urls_to_fetch(...)
103:TOPIC_DOMAINS
133:MAX_QUERY_CHARS
134:COPY_SENTENCE_CHARS
135:COPY_MATCHES
136:URL_RE
139:class SearchUnavailable:
143:def web_search(...)
161:def fetch_page(...)
166:class Doc:
176:UGC_DOMAINS
189:def _matches(...)
193:def publisher_kind(...)
199:def sanitize_query_for_free_engines(...)
228:def clean_query_for_free_search(...)
233:_ANCHOR_STOP_WORDS
239:_CONCISE_STOP_WORDS
251:def extract_article_subject(...)
272:def official_sites(...)
291:def _scoped_claim_text(...)
299:def _concise_numeric_query(...)
320:def build_queries(...)
355:def build_targeted_refinement_query(...)
370:def _rank_search_hits(...)
395:def _squash(...)
399:def is_copy_paste_source(...)
418:class Sources:
```

## bormochecker/spans.py

```python
14:TOKEN
17:FOLD
21:def _fold(...)
25:def _tokens(...)
29:def _token_end(...)
34:def find_span_bridging_parens(...)
62:def find_span(...)
82:def trim(...)
92:def snap(...)
98:def _has_substantive_issue(...)
119:def resolve_span_overlaps(...)
```

## bormochecker/textnorm.py

```python
13:NORMALIZER_VERSION
15:_RU_ENDINGS_RE
22:def normalize_token(...)
40:def normalize_tokens(...)
46:def lemmatize_text(...)
```

## bormochecker/web.py

```python
35:class ErrorResponse:
43:class QueueStatusResponse:
51:class HealthResponse:
64:INDEX
65:STATIC
78:ALLOWED_ORIGINS
104:def reset_rate_limiter(...)
110:def _check_rate_limit(...)
129:def validate_content_type_by_bytes(...)
141:async def security_middleware(...)
183:async def unhandled_exception_handler(...)
193:def _get_guest_modes(...)
198:def _get_auth_info(...)
233:def _verify_access(...)
257:GPU_BUSY_MODES
261:def _check_gpu_admission(...)
286:class GpuProcessLock:
332:def is_gpu_file_locked(...)
356:def index(...)
372:def healthz(...)
393:def readyz(...)
424:TURN
425:MAX_WAITING
445:def api_queue(...)
499:async def api_check(...)
```
