<script lang="ts">
  import { i18n, message, formatDate, formatWindow } from "../lib/i18n";
  import type { Retirement } from "../lib/types";
  let { retirement }: { retirement: Retirement } = $props();
  const end = $derived(
    new Date(Date.parse(retirement.final_issue + "T00:00:00Z") + 7 * 86400000)
      .toISOString()
      .slice(0, 10),
  );
</script>

<p
  class="notice retirement-notice"
  role="status"
  data-testid="retirement-notice"
>
  {message($i18n, "retirementNotice", {
    ended: formatDate(retirement.ended_utc, $i18n.language),
    target: formatWindow(retirement.final_issue, end, $i18n.language),
  })}
  <a href={retirement.source_url}>{$i18n.messages.retirementSource}</a>
</p>
