<script lang="ts">
  import { i18n, localizeText } from "../lib/i18n";
  import { provenanceRows } from "../lib/provenance";
  import { staticMode } from "../lib/data-client";
  import type { Meta } from "../lib/types";
  let { meta }: { meta: Meta } = $props();
  const provenance = $derived(provenanceRows(meta, $i18n.messages));
  const safeLink = (url: string) => /^https:\/\//.test(url);
</script>

<section
  id="about"
  class="about-panel"
  aria-label={$i18n.messages.aboutAndLimitations}
>
  <details open>
    <summary>{$i18n.messages.aboutLimitations}</summary>
    <div class="about-grid">
      <div>
        <h3>{$i18n.messages.whatIsADetection}</h3>
        <p>
          {$i18n.messages.theTargetIsAtLeastOne}
          {staticMode
            ? $i18n.messages.nearRealTime
            : $i18n.messages.standardProcessing}
          {$i18n.messages.targetDefinition}
        </p>
        <p>
          {$i18n.messages.detectionLimitations}
        </p>
        <h3>{$i18n.messages.observationCoverage}</h3>
        <p>
          {$i18n.messages.aDayMustBeAcquiredAnd}
        </p>
      </div>
      <div>
        <h3>
          {staticMode
            ? $i18n.messages.experimentalWeeklyEstimates
            : $i18n.messages.retrospectiveEstimates}
        </h3>
        {#if staticMode}<p>
            {$i18n.messages.liveLimitations}
          </p>{:else}
          <p>
            {$i18n.messages.retrospectiveLimitations}
          </p>
        {/if}
        {#if meta.limitations.length}<details class="api-limitations">
            <summary>{$i18n.messages.datasetSpecificLimitations}</summary
            >{#each meta.limitations as limitation}<p>
                {localizeText($i18n, limitation)}
              </p>{/each}
          </details>{/if}
        <h3>{$i18n.messages.sources}</h3>
        <p class="small">
          {$i18n.messages.placeNames}
          <a href="https://www.geonames.org/">{$i18n.messages.geonames}</a>
          (<a href="https://creativecommons.org/licenses/by/4.0/"
            >{$i18n.messages.ccBy40}</a
          >)
        </p>
        <p class="small">
          {$i18n.messages.provinceAndMunicipalityBoundaries}
          <a href="https://www.geoboundaries.org/"
            >{$i18n.messages.geoboundariesAdm2Adm3}</a
          >.
        </p>
        {#each meta.attributions as source}<p class="small">
            {#if safeLink(source.url)}<a href={source.url}>{source.name}</a
              >{:else}{source.name}{/if}
            {#if source.product}
              · {localizeText($i18n, source.product)}{/if}
            {#if source.provider}
              · {localizeText($i18n, source.provider)}{/if}
            {#if source.license}
              · {#if safeLink(source.licenseUrl)}<a href={source.licenseUrl}
                  >{localizeText($i18n, source.license)}</a
                >{:else}{localizeText($i18n, source.license)}{/if}
            {/if}
            {#if source.processing}<br />{localizeText(
                $i18n,
                source.processing,
              )}{/if}
            {#if source.citation}<br />{source.citation}{/if}
          </p>{:else}<p>
            {$i18n.messages.sourceCreditsAreUnavailableSeeThe}
            <a
              href="https://github.com/Cupesi/bolivia-fire/blob/main/datasets/dataset_card.md"
              >{$i18n.messages.datasetSourceTerms}</a
            >.
          </p>{/each}
      </div>
    </div>
    {#if provenance.length}<div class="provenance">
        <h3>{$i18n.messages.dataProvenance}</h3>
        <dl>
          {#each provenance as [name, value]}<div>
              <dt>{name}</dt>
              <dd class="hash">
                {#if name === $i18n.messages.panelSnapshot && typeof value === "string"}<details
                  >
                    <summary>{$i18n.messages.viewIdentifiers}</summary>
                    <pre>{value}</pre>
                  </details>{:else}{value}{/if}
              </dd>
            </div>{/each}
        </dl>
        <p class="small muted">
          {$i18n.messages.areaDefinition}
        </p>
      </div>{/if}
  </details>
</section>
