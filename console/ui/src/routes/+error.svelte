<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { TriangleAlert } from '@lucide/svelte';
</script>

<svelte:head><title>{page.status} · Data Proxy</title></svelte:head>

<div class="card mx-auto mt-16 max-w-md bg-base-100 card-border">
	<div class="card-body items-center text-center">
		<TriangleAlert size={32} class="text-warning" />
		<h1 class="card-title text-xl">
			{page.status === 403 ? 'Not allowed' : page.status === 404 ? 'Not found' : 'Something went wrong'}
		</h1>
		<p class="text-base-content/70">{page.error?.message ?? 'The page could not load.'}</p>
		<div class="card-actions">
			<a class="btn btn-primary btn-sm" href={resolve('/')}>Back to the console</a>
			<button
				class="btn btn-sm"
				type="button"
				onclick={() => {
					location.reload();
				}}>Try again</button
			>
		</div>
	</div>
</div>
