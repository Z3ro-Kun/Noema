import { useState } from 'react'
import {
  fetchContainers,
  fetchContentUnits,
  fetchEntities,
  fetchRelationships,
  fetchWorkInternal,
} from '../api/catalog'
import { useAsync } from '../hooks/useAsync'
import type {
  Container,
  ContentUnit,
  Entity,
  Relationship,
  WorkDetail as WorkDetailType,
} from '../types/api'

interface WorkDetailProps {
  workId: string
  onBack: () => void
}

interface Provenance {
  source_name?: string
  source_url?: string
  adapter?: string
  license_note?: string
}

interface AniListMetadata {
  format?: string
  season?: string
  season_year?: number
  episode_count?: number
  genres?: string[]
  status?: string
}

export default function WorkDetail({ workId, onBack }: WorkDetailProps) {
  const [containerId, setContainerId] = useState<string | null>(null)

  const { data: work } = useAsync<WorkDetailType>(() => fetchWorkInternal(workId), [workId])
  const { data: containers, loading, error } = useAsync<Container[]>(
    () => fetchContainers(workId),
    [workId],
  )
  const { data: entities } = useAsync<Entity[]>(() => fetchEntities(workId), [workId])
  const { data: relationships } = useAsync<Relationship[]>(
    () => fetchRelationships(workId),
    [workId],
  )
  const { data: units, loading: unitsLoading } = useAsync<ContentUnit[]>(
    () => (containerId ? fetchContentUnits(containerId) : Promise.resolve([])),
    [containerId],
  )

  const provenance = (work?.extra_metadata?.provenance ?? {}) as Provenance
  const anilist = work?.extra_metadata?.anilist as AniListMetadata | undefined
  const selected = containers?.find((container) => container.id === containerId) ?? null

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 px-6 py-4">
        <button
          type="button"
          onClick={onBack}
          className="mb-2 text-sm text-slate-400 hover:text-slate-200"
        >
          &larr; All works
        </button>
        <h1 className="text-xl font-semibold tracking-tight">{work?.title ?? 'Loading…'}</h1>
        {work && (
          <p className="text-sm text-slate-400">
            <span className="uppercase tracking-wide">{work.domain_slug}</span>
            {provenance.source_name ? ` · source: ${provenance.source_name}` : ''}
            {anilist?.format ? ` · ${anilist.format}` : ''}
            {anilist?.season_year ? ` · ${anilist.season ?? ''} ${anilist.season_year}` : ''}
          </p>
        )}
      </header>

      <main className="mx-auto grid max-w-5xl gap-6 px-6 py-8 md:grid-cols-[18rem_1fr]">
        <aside>
          <h2 className="mb-3 text-sm font-medium text-slate-400">
            {work?.domain_slug === 'anime' ? 'Episodes' : 'Containers'}
          </h2>
          {loading && <p className="text-slate-400">Loading&hellip;</p>}
          {error && <p className="text-red-400">Could not load containers ({error}).</p>}
          {containers && containers.length === 0 && (
            <p className="text-sm text-slate-400">No containers recorded for this work.</p>
          )}
          {containers && containers.length > 0 && (
            <ul className="max-h-96 divide-y divide-slate-800 overflow-y-auto rounded-lg border border-slate-800">
              {containers.map((container) => (
                <li key={container.id}>
                  <button
                    type="button"
                    onClick={() => setContainerId(container.id)}
                    className={`w-full px-3 py-2 text-left text-sm hover:bg-slate-900 ${
                      containerId === container.id ? 'bg-slate-900 text-slate-100' : 'text-slate-300'
                    }`}
                  >
                    <span className="text-slate-500">{container.sequence_number}.</span>{' '}
                    {container.title ?? container.container_type}
                    <span className="block text-xs text-slate-500">
                      {container.content_unit_count > 0
                        ? `${container.content_unit_count} units`
                        : 'no text available'}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}

          {work && (
            <section className="mt-6 rounded-lg border border-slate-800 bg-slate-900 p-3 text-xs">
              <h3 className="mb-2 font-medium text-slate-400">Provenance</h3>
              <dl className="space-y-1 text-slate-300">
                <dt className="text-slate-500">Adapter</dt>
                <dd>{provenance.adapter ?? 'unknown'}</dd>
                {provenance.source_url && (
                  <>
                    <dt className="text-slate-500">Source</dt>
                    <dd className="break-all">{provenance.source_url}</dd>
                  </>
                )}
                {provenance.license_note && (
                  <>
                    <dt className="text-slate-500">Licence</dt>
                    <dd>{provenance.license_note}</dd>
                  </>
                )}
              </dl>
            </section>
          )}
        </aside>

        <section className="space-y-8">
          {anilist?.genres && anilist.genres.length > 0 && (
            <section>
              <h2 className="mb-2 text-sm font-medium text-slate-400">
                Genres <span className="text-slate-600">(stated by source)</span>
              </h2>
              <div className="flex flex-wrap gap-2">
                {anilist.genres.map((genre) => (
                  <span
                    key={genre}
                    className="rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-300"
                  >
                    {genre}
                  </span>
                ))}
              </div>
            </section>
          )}

          <section data-testid="content-units">
            <h2 className="mb-3 text-sm font-medium text-slate-400">Content units</h2>
            {!containerId && (
              <p className="text-slate-400">Select a container to read its passages.</p>
            )}
            {containerId && unitsLoading && (
              <p className="text-slate-400">Loading passages&hellip;</p>
            )}
            {containerId && !unitsLoading && units && units.length === 0 && (
              <p className="rounded-lg border border-slate-800 bg-slate-900 p-4 text-sm text-slate-400">
                No text is available for{' '}
                {selected?.title ?? `${selected?.container_type} ${selected?.sequence_number}`}.
                This source provides episode structure and metadata, but no dialogue or script.
              </p>
            )}
            {containerId && units && units.length > 0 && (
              <ol className="space-y-3">
                {units.map((unit) => (
                  <li key={unit.id} className="flex gap-3">
                    <span className="pt-1 text-xs text-slate-600">{unit.sequence_number}</span>
                    <div className="space-y-2">
                      {/* A third party's description of the episode is never
                          presented as the episode's own words. */}
                      {unit.text_tier === 'summary' && (
                        <p className="flex flex-wrap items-center gap-2 text-xs">
                          <span className="rounded bg-amber-900/40 px-2 py-0.5 uppercase tracking-wide text-amber-300">
                            Summary
                          </span>
                          <span className="text-slate-500">
                            not original dialogue &middot; from{' '}
                            {unit.text_source?.source_name ?? 'an external source'}
                            {unit.text_source?.licence ? ` · ${unit.text_source.licence}` : ''}
                          </span>
                          {unit.text_source?.source_url && (
                            <a
                              href={unit.text_source.source_url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-slate-400 underline hover:text-slate-200"
                            >
                              source
                            </a>
                          )}
                        </p>
                      )}
                      <p className="text-slate-200">{unit.text_content}</p>
                      {unit.text_source?.requires_attribution && unit.text_source.attribution_text && (
                        <p className="text-xs text-slate-500">
                          {unit.text_source.attribution_text}
                        </p>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </section>

          {relationships && relationships.length > 0 && (
            <section data-testid="relationships">
              <h2 className="mb-3 text-sm font-medium text-slate-400">Related works</h2>
              <ul className="space-y-2">
                {relationships.map((relation) => (
                  <li key={relation.id} className="flex items-center gap-2 text-sm">
                    <span className="rounded bg-slate-800 px-2 py-0.5 text-xs uppercase tracking-wide text-slate-400">
                      {relation.predicate.replace(/_/g, ' ')}
                    </span>
                    <span className="text-slate-200">{relation.object_title ?? relation.object_id}</span>
                    {/* Source-stated vs computed must never look the same. */}
                    <span className="text-xs text-slate-500">
                      {relation.source === 'source'
                        ? `stated by ${relation.method?.replace('_relation', '') ?? 'source'}`
                        : `computed (${relation.method})`}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {entities && entities.length > 0 && (
            <section data-testid="entities">
              <h2 className="mb-3 text-sm font-medium text-slate-400">
                Characters <span className="text-slate-600">({entities.length})</span>
              </h2>
              <ul className="flex flex-wrap gap-2">
                {entities.map((entity) => (
                  <li
                    key={entity.id}
                    className="rounded-full border border-slate-700 px-3 py-1 text-sm text-slate-300"
                  >
                    {entity.name}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </section>
      </main>
    </div>
  )
}
