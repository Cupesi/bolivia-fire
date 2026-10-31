<script lang="ts">
  import { i18n } from "../lib/i18n";
  import type { Cell } from "../lib/types";
  import { nearestPlaceText } from "../lib/map-detail";
  let { cell }: { cell: Cell } = $props();
</script>

<div class="cell-places" data-testid="cell-places">
  {#if cell.municipality}<p>
      {$i18n.messages.municipality3}
      {cell.municipality}
    </p>{/if}
  {#if cell.province}<p>{$i18n.messages.province3} {cell.province}</p>{/if}
  {#if cell.department}<p>
      {$i18n.messages.department3}
      {cell.department}
    </p>{/if}
  {#if cell.nearestPlace}<p>{nearestPlaceText(cell, $i18n)}</p>{/if}
  {#if cell.populatedPlaces?.length}<p>
      {$i18n.messages.placesInCell}
      {cell.populatedPlaces.map((place) => place.name).join("; ")}
    </p>{/if}
</div>

<style>
  .cell-places {
    font-size: 0.8rem;
    line-height: 1.55;
  }
  p {
    margin: 0.2rem 0;
  }
</style>
