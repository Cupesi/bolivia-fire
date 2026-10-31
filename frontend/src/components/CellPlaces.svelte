<script lang="ts">
  import type { Cell } from "../lib/types";
  import { nearestPlaceText } from "../lib/map-detail";
  let { cell }: { cell: Cell } = $props();
</script>

<div class="cell-places" data-testid="cell-places">
  {#if cell.municipality}<p>Municipality: {cell.municipality}</p>{/if}
  {#if cell.province}<p>Province: {cell.province}</p>{/if}
  {#if cell.department}<p>Department: {cell.department}</p>{/if}
  {#if cell.nearestPlace}<p>{nearestPlaceText(cell)}</p>{/if}
  {#if cell.populatedPlaces?.length}<p>
      Places in cell: {cell.populatedPlaces
        .map((place) => place.name)
        .join("; ")}
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
