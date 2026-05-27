"use client";

import { useState } from "react";

type Candidate = {
  rank?: number;
  vendor?: string;
  series?: string;
  model?: string;
  device_class?: string;
  rated_current_a?: number;
  trip_curve?: string | null;
  breaking_capacity_ka?: number;
  confidence?: number;
  verdict?: string;
  why_this_candidate?: string;
  why_not_best?: string;
  price_found?: boolean;
  price_article?: string | null;
  price_title?: string | null;
  price_rub?: number | null;
  price_currency?: string | null;
  price_source_domain?: string | null;
  price_source_type?: string | null;
  price_product_url?: string | null;
};

type NormativeRef = {
  doc_title?: string;
  section_hint?: string;
  source_file?: string;
  chunk_id?: string;
};

type EvidenceItem = {
  doc_title?: string;
  section_hint?: string;
  score?: number;
  excerpt?: string;
};

type EngineeringCheck = {
  title?: string;
  status?: string;
  details?: string;
  reference?: {
    doc_title?: string;
    source_file?: string;
    chunk_id?: string;
    section_hint?: string;
    score?: number;
  };
};

type ReviewBlock = {
  review_summary?: string;
  positive_points?: string[];
  negative_points?: string[];
  risk_flags?: string[];
  manual_caution?: string;
  source_notes?: string[];
};

type CriticIssue = {
  type?: string;
  severity?: string;
  message?: string;
  evidence?: string;
};

type LlmCritic = {
  critic_verdict?: string;
  critic_score?: number;
  risk_level?: string;
  summary?: string;
  issues?: CriticIssue[];
  recommendation?: string;
  _critic_model?: string;
  _reasoning_effort?: string;
  _stale?: boolean;
  _warning?: string;
};

type ResultPayload = {
  tag?: string;
  query?: string;
  verdict?: string;
  confidence?: number;
  why_this_candidate?: string;
  llm_readable_explanation?: string;
  llm_alternative_summary?: string;
  llm_manual_review_note?: string;
  summary_bullets?: string[];
  engineering_checks?: EngineeringCheck[];
  normative_refs?: NormativeRef[];
  evidence_top?: EvidenceItem[];
  normative_hits?: EvidenceItem[];
  candidate?: Candidate;
  candidate_options?: Candidate[];
  llm_critic?: LlmCritic | null;
  llm_critic_error?: string;
};

type ApiResponse = {
  ok: boolean;
  tag?: string;
  result?: ResultPayload;
  review_block?: ReviewBlock | null;
  price_rows_fallback?: any[];
  error?: string;
  message?: string;
};

function formatPrice(value?: number | null, currency?: string | null) {
  if (value === null || value === undefined) return "—";
  const formatted = new Intl.NumberFormat("ru-RU", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(value);
  return `${formatted} ${currency || "RUB"}`;
}

function verdictLabel(verdict?: string) {
  if (!verdict) return "—";
  if (verdict === "supported") return "Поддержано";
  if (verdict === "supported_with_conditions") return "Поддержано с условиями";
  if (verdict === "manual_review") return "Нужна ручная проверка";
  if (verdict === "rejected") return "Отклонено";
  return verdict;
}

function criticVerdictLabel(verdict?: string) {
  if (!verdict) return "—";
  if (verdict === "accepted") return "Принято";
  if (verdict === "accepted_with_conditions") return "Принято с условиями";
  if (verdict === "manual_review_required") return "Нужна ручная проверка";
  if (verdict === "reject_or_recalculate") return "Отклонить / пересчитать";
  return verdict;
}

function criticRiskLabel(risk?: string) {
  if (!risk) return "—";
  if (risk === "low") return "Низкий";
  if (risk === "medium") return "Средний";
  if (risk === "high") return "Высокий";
  return risk;
}

function criticBadgeClass(value?: string) {
  const v = value || "";

  if (v === "accepted" || v === "low") {
    return "bg-green-100 text-green-800";
  }

  if (
    v === "accepted_with_conditions" ||
    v === "manual_review_required" ||
    v === "medium"
  ) {
    return "bg-amber-100 text-amber-800";
  }

  return "bg-red-100 text-red-800";
}

function sourceLabel(candidate: Candidate) {
  if (!candidate.price_found) return "—";

  const sourceType = candidate.price_source_type || "";
  const domain = candidate.price_source_domain || "";

  if (sourceType.includes("official")) return "Офиц. каталог";
  if (sourceType.includes("seller")) return "Продавец";
  if (domain) return domain;

  return "Источник";
}

function sourceBadgeClass(candidate: Candidate) {
  if (!candidate.price_found) {
    return "bg-neutral-100 text-neutral-600";
  }

  const sourceType = candidate.price_source_type || "";

  if (sourceType.includes("official")) {
    return "bg-green-100 text-green-700";
  }

  if (sourceType.includes("seller")) {
    return "bg-amber-100 text-amber-700";
  }

  return "bg-blue-100 text-blue-700";
}

function priceComment(candidate: Candidate) {
  if (!candidate.price_found) return "Цена не найдена";

  const sourceType = candidate.price_source_type || "";
  if (sourceType.includes("official")) {
    return "Цена взята из официального каталога производителя.";
  }

  return "Цена взята из внешнего источника и требует проверки.";
}

export default function HomePage() {
  const [tag, setTag] = useState("К6");
  const [refreshAi, setRefreshAi] = useState(false);
  const [refreshCritic, setRefreshCritic] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [data, setData] = useState<ApiResponse | null>(null);

  async function handleCheck() {
    const tagValue = tag.trim();

    if (!tagValue) {
      setError("Введите тег электроприёмника, например К6 или ГГ.1");
      return;
    }

    setLoading(true);
    setError("");
    setData(null);

    try {
      const params = new URLSearchParams();

      if (refreshAi) {
        params.set("refresh_ai", "true");
      }

      if (refreshCritic) {
        params.set("refresh_critic", "true");
      }

      const queryString = params.toString();
      const apiUrl = `http://127.0.0.1:8000/api/tag/${encodeURIComponent(
        tagValue
      )}${queryString ? `?${queryString}` : ""}`;

      const response = await fetch(apiUrl);

      const rawText = await response.text();

      let json: ApiResponse | null = null;

      try {
        json = rawText ? JSON.parse(rawText) : null;
      } catch {
        throw new Error(
          `API вернул не JSON. HTTP ${response.status}. Ответ: ${rawText.slice(
            0,
            300
          )}`
        );
      }

      if (!response.ok) {
        throw new Error(
          json?.message || json?.error || `HTTP ${response.status}`
        );
      }

      setData(json);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Неизвестная ошибка");
    } finally {
      setLoading(false);
    }
  }

  const result = data?.result;
  const candidate = result?.candidate;
  const options = result?.candidate_options || [];
  const summaryBullets = result?.summary_bullets || [];
  const engineeringChecks = result?.engineering_checks || [];
  const normativeRefs = result?.normative_refs || [];
  const evidenceTop = result?.evidence_top || [];
  const reviewBlock = data?.review_block;
  const llmCritic = result?.llm_critic;

  return (
    <main className="min-h-screen bg-neutral-100 p-8">
      <div className="mx-auto max-w-7xl">
        <h1 className="text-4xl font-bold tracking-tight text-neutral-900">
          Boiler Elec AI
        </h1>
        <p className="mt-2 text-neutral-600">
          Подбор аппарата защиты с нормативным и ценовым обоснованием
        </p>

        <section className="mt-8 rounded-3xl bg-white p-6 shadow-sm">
          <label className="block text-sm font-medium text-neutral-700">
            Тег ЭП
          </label>

          <div className="mt-4 grid gap-3 text-sm text-neutral-700 sm:grid-cols-2">
            <label className="flex items-center gap-2 rounded-2xl bg-neutral-50 p-3">
              <input
                type="checkbox"
                checked={refreshCritic}
                onChange={(event) => setRefreshCritic(event.target.checked)}
              />
              <span>Обновить Grok-критику</span>
            </label>

            <label className="flex items-center gap-2 rounded-2xl bg-neutral-50 p-3">
              <input
                type="checkbox"
                checked={refreshAi}
                onChange={(event) => setRefreshAi(event.target.checked)}
              />
              <span>Пересчитать OpenAI-слои</span>
            </label>
          </div>

          <div className="mt-3 flex flex-col gap-3 sm:flex-row">
            <input
              value={tag}
              onChange={(e) => setTag(e.target.value)}
              className="w-full rounded-2xl border border-neutral-300 px-4 py-3 outline-none transition focus:border-neutral-500 sm:max-w-xs"
              placeholder="Например: К6"
            />
            <button
              onClick={handleCheck}
              disabled={loading}
              className="rounded-2xl bg-neutral-900 px-6 py-3 font-medium text-white transition hover:bg-neutral-800 disabled:opacity-50"
            >
              {loading ? "Загрузка..." : "Проверить"}
            </button>
          </div>

          {error && (
            <p className="mt-4 text-sm text-red-600">
              Ошибка: {error}
            </p>
          )}
        </section>

        {result && candidate && (
          <>
            <section className="mt-8 rounded-3xl bg-white p-6 shadow-sm">
              <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div>
                  <p className="text-sm font-medium uppercase tracking-wide text-neutral-500">
                    Результат по тегу
                  </p>
                  <h2 className="mt-1 text-3xl font-bold text-neutral-900">
                    {result.tag || "—"}
                  </h2>
                  <p className="mt-3 text-lg text-neutral-800">
                    <span className="font-semibold">Кандидат:</span>{" "}
                    {candidate.vendor} {candidate.series} {candidate.model}
                  </p>
                </div>

                <div className="grid gap-3 sm:grid-cols-2 lg:w-[320px]">
                  <div className="rounded-2xl bg-neutral-50 p-4">
                    <p className="text-xs uppercase tracking-wide text-neutral-500">
                      Вердикт
                    </p>
                    <p className="mt-2 font-semibold text-neutral-900">
                      {verdictLabel(result.verdict)}
                    </p>
                  </div>

                  <div className="rounded-2xl bg-neutral-50 p-4">
                    <p className="text-xs uppercase tracking-wide text-neutral-500">
                      Confidence
                    </p>
                    <p className="mt-2 font-semibold text-neutral-900">
                      {result.confidence ?? "—"}
                    </p>
                  </div>
                </div>
              </div>

              <div className="mt-6 grid gap-6 lg:grid-cols-2">
                <div className="rounded-2xl bg-neutral-50 p-5">
                  <h3 className="text-lg font-semibold text-neutral-900">
                    Почему выбран кандидат
                  </h3>
                  <p className="mt-3 whitespace-pre-line text-sm leading-6 text-neutral-700">
                    {result.why_this_candidate || "Нет данных"}
                  </p>
                </div>

                <div className="rounded-2xl bg-neutral-50 p-5">
                  <h3 className="text-lg font-semibold text-neutral-900">
                    LLM-объяснение
                  </h3>
                  <p className="mt-3 whitespace-pre-line text-sm leading-6 text-neutral-700">
                    {result.llm_readable_explanation || "Нет данных"}
                  </p>
                </div>
              </div>
            </section>

            <section className="mt-8 grid gap-6 lg:grid-cols-2">
              <div className="rounded-3xl bg-white p-6 shadow-sm">
                <h3 className="text-xl font-semibold text-neutral-900">
                  Краткие выводы
                </h3>

                {summaryBullets.length > 0 ? (
                  <ul className="mt-4 space-y-3 text-sm leading-6 text-neutral-700">
                    {summaryBullets.map((item, idx) => (
                      <li key={idx} className="flex gap-3">
                        <span className="mt-2 h-2 w-2 rounded-full bg-neutral-900" />
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-4 text-sm text-neutral-600">Нет данных</p>
                )}
              </div>

              <div className="rounded-3xl bg-white p-6 shadow-sm">
                <h3 className="text-xl font-semibold text-neutral-900">
                  Поисковый запрос RAG
                </h3>
                <div className="mt-4 rounded-2xl bg-neutral-50 p-4">
                  <p className="text-sm leading-6 text-neutral-700">
                    {result.query || "Нет данных"}
                  </p>
                </div>
              </div>
            </section>

                        {(llmCritic || result.llm_critic_error) && (
              <section className="mt-8 rounded-3xl bg-white p-6 shadow-sm">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                  <div>
                    <h3 className="text-2xl font-semibold text-neutral-900">
                      Внешняя LLM-критика результата
                    </h3>
                    <p className="mt-2 text-sm leading-6 text-neutral-500">
                      Критик не меняет выбранный аппарат, а проверяет согласованность
                      JSON/API, LLM-объяснения, цены, требований и нормативных оснований.
                    </p>
                  </div>

                  {llmCritic && (
                    <div className="grid gap-3 sm:grid-cols-3 lg:min-w-[520px]">
                      <div className="rounded-2xl bg-neutral-50 p-4">
                        <p className="text-xs uppercase tracking-wide text-neutral-500">
                          Вердикт критика
                        </p>
                        <p
                          className={`mt-2 inline-flex rounded-full px-3 py-1 text-sm font-semibold ${criticBadgeClass(
                            llmCritic.critic_verdict
                          )}`}
                        >
                          {criticVerdictLabel(llmCritic.critic_verdict)}
                        </p>
                      </div>

                      <div className="rounded-2xl bg-neutral-50 p-4">
                        <p className="text-xs uppercase tracking-wide text-neutral-500">
                          Оценка
                        </p>
                        <p className="mt-2 font-semibold text-neutral-900">
                          {llmCritic.critic_score ?? "—"}
                        </p>
                      </div>

                      <div className="rounded-2xl bg-neutral-50 p-4">
                        <p className="text-xs uppercase tracking-wide text-neutral-500">
                          Риск
                        </p>
                        <p
                          className={`mt-2 inline-flex rounded-full px-3 py-1 text-sm font-semibold ${criticBadgeClass(
                            llmCritic.risk_level
                          )}`}
                        >
                          {criticRiskLabel(llmCritic.risk_level)}
                        </p>
                      </div>
                    </div>
                  )}
                </div>

                {result.llm_critic_error && !llmCritic && (
                  <p className="mt-5 rounded-2xl bg-red-50 p-4 text-sm leading-6 text-red-700">
                    Критика не выполнена: {result.llm_critic_error}
                  </p>
                )}

                {llmCritic?._stale && (
                  <p className="mt-5 rounded-2xl bg-amber-50 p-4 text-sm leading-6 text-amber-800">
                    Показан ранее сохраненный результат критики.{" "}
                    {llmCritic._warning || ""}
                  </p>
                )}

                {llmCritic?.summary && (
                  <div className="mt-5 rounded-2xl bg-neutral-50 p-5">
                    <h4 className="font-semibold text-neutral-900">
                      Итог критики
                    </h4>
                    <p className="mt-2 text-sm leading-6 text-neutral-700">
                      {llmCritic.summary}
                    </p>
                  </div>
                )}

                {llmCritic?.issues && llmCritic.issues.length > 0 && (
                  <div className="mt-6">
                    <h4 className="font-semibold text-neutral-900">
                      Найденные замечания
                    </h4>

                    <ul className="mt-3 space-y-3 text-sm leading-6 text-neutral-700">
                      {llmCritic.issues.map((issue, idx) => (
                        <li key={idx} className="rounded-2xl bg-neutral-50 p-4">
                          <div className="flex flex-wrap items-center gap-2">
                            <span
                              className={`rounded-full px-3 py-1 text-xs font-semibold ${criticBadgeClass(
                                issue.severity
                              )}`}
                            >
                              {issue.severity || "issue"}
                            </span>
                            <span className="font-semibold text-neutral-900">
                              {issue.type || "Замечание"}
                            </span>
                          </div>

                          <p className="mt-2 text-neutral-700">
                            {issue.message || "Описание отсутствует"}
                          </p>

                          {issue.evidence && (
                            <p className="mt-2 text-xs leading-5 text-neutral-500">
                              Основание: {issue.evidence}
                            </p>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {llmCritic?.recommendation && (
                  <div className="mt-6 rounded-2xl bg-neutral-900 p-5 text-white">
                    <h4 className="font-semibold">
                      Рекомендация критика
                    </h4>
                    <p className="mt-2 text-sm leading-6 text-neutral-100">
                      {llmCritic.recommendation}
                    </p>
                  </div>
                )}
              </section>
            )}
            
            <section className="mt-8 rounded-3xl bg-white p-6 shadow-sm">
              <h3 className="text-2xl font-semibold text-neutral-900">
                Что инженер должен проверить
              </h3>

              {engineeringChecks.length > 0 ? (
                <ul className="mt-5 space-y-4 text-sm leading-6 text-neutral-700">
                  {engineeringChecks.map((check, idx) => (
                    <li key={idx} className="rounded-2xl bg-neutral-50 p-4">
                      <div className="font-semibold text-neutral-900">
                        {check.title || "Проверка"}
                      </div>

                      <div className="mt-1">
                        <span className="font-medium">Статус:</span>{" "}
                        {check.status || "—"}
                      </div>

                      <div className="mt-1">
                        {check.details || "Нет описания"}
                      </div>

                      {check.reference?.doc_title && (
                        <div className="mt-2 text-xs text-neutral-500">
                          Источник: {check.reference.doc_title}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-4 text-sm text-neutral-600">
                  Дополнительные инженерные проверки не сформированы.
                </p>
              )}
            </section>

            <section className="mt-8 rounded-3xl bg-white p-6 shadow-sm">
              <h3 className="text-2xl font-semibold text-neutral-900">
                Нормативные ссылки
              </h3>

              {normativeRefs.length > 0 ? (
                <ul className="mt-5 space-y-4 text-sm leading-6 text-neutral-700">
                  {normativeRefs.map((ref, idx) => (
                    <li key={idx} className="rounded-2xl bg-neutral-50 p-4">
                      <div className="font-semibold text-neutral-900">
                        {ref.doc_title || "Нормативный документ"}
                      </div>

                      <div className="mt-1 text-neutral-700">
                        {ref.section_hint || "Раздел не указан"}
                      </div>

                      <div className="mt-2 text-xs text-neutral-500">
                        {ref.source_file || "Файл не указан"}
                        {ref.chunk_id ? ` · ${ref.chunk_id}` : ""}
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-4 text-sm text-neutral-600">
                  Нормативные ссылки не найдены.
                </p>
              )}
            </section>

            <section className="mt-8 rounded-3xl bg-white p-6 shadow-sm">
              <h3 className="text-2xl font-semibold text-neutral-900">
                Нормативные основания
              </h3>

              {evidenceTop.length > 0 ? (
                <div className="mt-5 space-y-5">
                  {evidenceTop.map((item, idx) => (
                    <article key={idx} className="rounded-2xl bg-neutral-50 p-5">
                      <h4 className="font-semibold text-neutral-900">
                        {item.doc_title || "Нормативный фрагмент"}
                      </h4>

                      {item.section_hint && (
                        <p className="mt-2 text-sm text-neutral-500">
                          {item.section_hint}
                        </p>
                      )}

                      <p className="mt-2 text-sm text-neutral-700">
                        <span className="font-medium">Score:</span>{" "}
                        {item.score ?? "—"}
                      </p>

                      <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap rounded-2xl bg-white p-4 text-xs leading-5 text-neutral-700">
                        {item.excerpt || "Фрагмент отсутствует"}
                      </pre>
                    </article>
                  ))}
                </div>
              ) : (
                <p className="mt-4 text-sm text-neutral-600">
                  Нормативные основания не найдены.
                </p>
              )}
            </section>
            
            {reviewBlock && (
              <section className="mt-8 rounded-3xl bg-white p-6 shadow-sm">
                <h3 className="text-2xl font-semibold text-neutral-900">
                  Обзор открытых источников
                </h3>

                <p className="mt-2 text-sm text-neutral-500">
                  Этот блок носит справочный характер и не заменяет расчёт и нормативную проверку.
                </p>

                {reviewBlock.review_summary && (
                  <p className="mt-4 text-sm leading-6 text-neutral-700">
                    {reviewBlock.review_summary}
                  </p>
                )}

                <div className="mt-6 grid gap-6 lg:grid-cols-2">
                  <div className="rounded-2xl bg-neutral-50 p-5">
                    <h4 className="font-semibold text-neutral-900">
                      Положительные сигналы
                    </h4>
                    <ul className="mt-3 space-y-2 text-sm leading-6 text-neutral-700">
                      {(reviewBlock.positive_points || []).map((item, idx) => (
                        <li key={idx}>• {item}</li>
                      ))}
                    </ul>
                  </div>

                  <div className="rounded-2xl bg-neutral-50 p-5">
                    <h4 className="font-semibold text-neutral-900">
                      Негативные сигналы
                    </h4>
                    <ul className="mt-3 space-y-2 text-sm leading-6 text-neutral-700">
                      {(reviewBlock.negative_points || []).map((item, idx) => (
                        <li key={idx}>• {item}</li>
                      ))}
                    </ul>
                  </div>
                </div>

                <div className="mt-6 grid gap-6 lg:grid-cols-2">
                  <div className="rounded-2xl bg-neutral-50 p-5">
                    <h4 className="font-semibold text-neutral-900">
                      Риски
                    </h4>
                    <ul className="mt-3 space-y-2 text-sm leading-6 text-neutral-700">
                      {(reviewBlock.risk_flags || []).map((item, idx) => (
                        <li key={idx}>• {item}</li>
                      ))}
                    </ul>
                  </div>

                  <div className="rounded-2xl bg-neutral-50 p-5">
                    <h4 className="font-semibold text-neutral-900">
                      Ручная проверка по отзывам
                    </h4>
                    <p className="mt-3 text-sm leading-6 text-neutral-700">
                      {reviewBlock.manual_caution || "Нет данных"}
                    </p>
                  </div>
                </div>

                {(reviewBlock.source_notes || []).length > 0 && (
                  <div className="mt-6 rounded-2xl bg-neutral-50 p-5">
                    <h4 className="font-semibold text-neutral-900">
                      Примечания по источникам
                    </h4>
                    <ul className="mt-3 space-y-2 text-sm leading-6 text-neutral-700">
                      {(reviewBlock.source_notes || []).map((item, idx) => (
                        <li key={idx}>• {item}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </section>
            )}

            <section className="mt-8 rounded-3xl bg-white p-6 shadow-sm">
              <div className="flex items-center justify-between">
                <h3 className="text-2xl font-semibold text-neutral-900">
                  Кандидаты для выбора
                </h3>
                <span className="rounded-full bg-neutral-100 px-3 py-1 text-sm text-neutral-600">
                  {options.length} вариантов
                </span>
              </div>

              <div className="mt-6 overflow-x-auto">
                <table className="min-w-full border-separate border-spacing-y-3">
                  <thead>
                    <tr className="text-left text-sm text-neutral-500">
                      <th className="px-3 py-2">Ранг</th>
                      <th className="px-3 py-2">Кандидат</th>
                      <th className="px-3 py-2">Цена</th>
                      <th className="px-3 py-2">Источник</th>
                      <th className="px-3 py-2">Артикул</th>
                      <th className="px-3 py-2">Verdict</th>
                      <th className="px-3 py-2">Confidence</th>
                      <th className="px-3 py-2">Почему не лучший</th>
                      <th className="px-3 py-2">Комментарий по цене</th>
                    </tr>
                  </thead>

                  <tbody>
                    {options.map((opt, idx) => (
                      <tr
                        key={`${opt.vendor}-${opt.series}-${opt.model}-${idx}`}
                        className="rounded-2xl bg-neutral-50 text-sm text-neutral-800"
                      >
                        <td className="rounded-l-2xl px-3 py-4 align-top font-medium">
                          {opt.rank ?? idx + 1}
                        </td>

                        <td className="px-3 py-4 align-top">
                          <div className="font-semibold text-neutral-900">
                            {opt.vendor} {opt.series}
                          </div>
                          <div className="mt-1 text-neutral-600">
                            {opt.model}
                          </div>
                        </td>

                        <td className="px-3 py-4 align-top font-medium">
                          {formatPrice(opt.price_rub, opt.price_currency)}
                        </td>

                        <td className="px-3 py-4 align-top">
                          <div
                            className={`inline-flex rounded-full px-3 py-1 text-xs font-medium ${sourceBadgeClass(
                              opt
                            )}`}
                          >
                            {sourceLabel(opt)}
                          </div>
                        </td>

                        <td className="px-3 py-4 align-top">
                          {opt.price_article || "—"}
                        </td>

                        <td className="px-3 py-4 align-top">
                          {verdictLabel(opt.verdict)}
                        </td>

                        <td className="px-3 py-4 align-top">
                          {opt.confidence ?? "—"}
                        </td>

                        <td className="px-3 py-4 align-top max-w-xs text-neutral-600">
                          {opt.why_not_best || "—"}
                        </td>

                        <td className="rounded-r-2xl px-3 py-4 align-top max-w-sm text-neutral-600">
                          {priceComment(opt)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </div>
    </main>
  );
}