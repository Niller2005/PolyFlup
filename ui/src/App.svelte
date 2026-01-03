<script>
    import { onMount } from 'svelte';
    import { Activity, TrendingUp, Award, ShieldAlert, Target, RefreshCw, Clock, Wallet, Percent, ArrowUpRight, ArrowDownRight } from 'lucide-svelte';
    import * as Card from "$lib/components/ui/card/index.js";
    import * as Table from "$lib/components/ui/table/index.js";
    import { Badge } from "$lib/components/ui/badge/index.js";
    import { Button } from "$lib/components/ui/button/index.js";
    import * as Chart from "$lib/components/ui/chart/index.js";
    import { AreaChart, BarChart } from "layerchart";

    let stats = null;
    let lastUpdate = new Date().toLocaleTimeString();
    let loading = true;
    let error = null;

    async function fetchStats() {
        try {
            const hostname = window.location.hostname;
            const res = await fetch(`http://${hostname}:3001/api/stats`);
            if (!res.ok) throw new Error('Failed to fetch stats');
            stats = await res.json();
            lastUpdate = new Date().toLocaleTimeString();
            loading = false;
        } catch (e) {
            console.error(e);
            error = e.message;
        }
    }

    onMount(() => {
        fetchStats();
        const interval = setInterval(fetchStats, 5000);
        return () => clearInterval(interval);
    });

    $: winRateNum = stats ? Number(stats.summary.wins / stats.summary.settled * 100) : 0;
    $: totalRoiNum = stats ? Number(stats.summary.total_pnl / stats.summary.invested * 100) : 0;
    $: winRate = winRateNum.toFixed(1);
    $: totalRoi = totalRoiNum.toFixed(1);

    // Chart Data preparation
    $: pnlData = stats ? (() => {
        const data = [];
        let cumsum = 0;
        stats.pnl_history.forEach(h => {
            cumsum += h.pnl_usd;
            data.push({ date: new Date(h.timestamp), pnl: cumsum });
        });
        return data.slice(-50);
    })() : [];

    $: assetData = stats ? stats.per_symbol.map(s => ({
        symbol: s.symbol,
        pnl: s.pnl
    })) : [];

    const pnlConfig = {
        pnl: {
            label: "PnL ($)",
            color: "var(--color-primary)"
        }
    };

    const assetConfig = {
        pnl: {
            label: "PnL ($)",
            color: "var(--color-primary)"
        }
    };
</script>

<div class="min-h-screen bg-slate-50 p-4 md:p-8 font-sans">
    <div class="max-w-7xl mx-auto space-y-8">
        <!-- Header -->
        <div class="flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div>
                <h1 class="text-3xl font-bold tracking-tight text-slate-900 flex items-center gap-3">
                    <div class="p-2 bg-primary rounded-lg text-primary-foreground">
                        <Activity size={24} />
                    </div>
                    PolyAstra Dashboard
                </h1>
                <p class="text-muted-foreground mt-1 font-medium text-sm">Automated Trading Intelligence</p>
            </div>
            <div class="flex items-center gap-3 bg-white p-2 pl-4 rounded-full shadow-sm border border-border">
                <div class="flex flex-col">
                    <span class="text-[10px] text-muted-foreground uppercase font-bold tracking-wider leading-none">Last sync</span>
                    <span class="text-sm font-mono font-bold text-slate-700">{lastUpdate}</span>
                </div>
                <Button variant="ghost" size="icon" on:click={fetchStats} class="rounded-full h-10 w-10">
                    <RefreshCw size={18} class="text-primary" />
                </Button>
            </div>
        </div>

        {#if error}
            <div class="bg-destructive/10 border border-destructive/20 text-destructive p-4 rounded-xl flex items-center gap-3">
                <ShieldAlert size={20} />
                <p class="font-medium text-sm">Error connecting to backend: {error}</p>
            </div>
        {/if}

        {#if stats}
            <!-- Stats Grid -->
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 text-sm">
                <Card.Root>
                    <Card.Header class="pb-2">
                        <Card.Description class="text-[10px] uppercase font-bold tracking-wider flex items-center gap-2">
                            <Wallet size={14} /> Total PnL
                        </Card.Description>
                        <Card.Title class="text-3xl {stats.summary.total_pnl >= 0 ? 'text-emerald-600' : 'text-rose-600'}">
                            ${stats.summary.total_pnl.toFixed(2)}
                        </Card.Title>
                    </Card.Header>
                    <Card.Content>
                        <div class="flex items-center gap-1 text-[11px] font-medium {totalRoiNum >= 0 ? 'text-emerald-600' : 'text-rose-600'}">
                            {#if totalRoiNum >= 0}<ArrowUpRight size={14} />{:else}<ArrowDownRight size={14} />{/if}
                            {totalRoi}% Total ROI
                        </div>
                    </Card.Content>
                </Card.Root>

                <Card.Root>
                    <Card.Header class="pb-2">
                        <Card.Description class="text-[10px] uppercase font-bold tracking-wider flex items-center gap-2">
                            <Percent size={14} /> Win Rate
                        </Card.Description>
                        <Card.Title class="text-3xl {winRateNum >= 50 ? 'text-emerald-600' : 'text-rose-600'}">
                            {winRate}%
                        </Card.Title>
                    </Card.Header>
                    <Card.Content>
                        <div class="text-[11px] font-medium text-muted-foreground">
                            {stats.summary.wins} Wins / {stats.summary.settled} Settled
                        </div>
                    </Card.Content>
                </Card.Root>

                <Card.Root>
                    <Card.Header class="pb-2">
                        <Card.Description class="text-[10px] uppercase font-bold tracking-wider flex items-center gap-2">
                            <Clock size={14} /> Stop Losses
                        </Card.Description>
                        <Card.Title class="text-3xl text-rose-600">
                            {stats.summary.stop_losses || 0}
                        </Card.Title>
                    </Card.Header>
                    <Card.Content>
                        <div class="text-[11px] font-medium text-muted-foreground italic">
                            Risk management efficiency
                        </div>
                    </Card.Content>
                </Card.Root>

                <Card.Root>
                    <Card.Header class="pb-2">
                        <Card.Description class="text-[10px] uppercase font-bold tracking-wider flex items-center gap-2">
                            <Target size={14} /> Take Profits
                        </Card.Description>
                        <Card.Title class="text-3xl text-emerald-600">
                            {stats.summary.take_profits || 0}
                        </Card.Title>
                    </Card.Header>
                    <Card.Content>
                        <div class="text-[11px] font-medium text-muted-foreground italic">
                            Profit target hit rate
                        </div>
                    </Card.Content>
                </Card.Root>
            </div>

            <!-- Charts -->
            <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <Card.Root class="lg:col-span-2">
                    <Card.Header>
                        <Card.Title class="text-lg flex items-center gap-2">
                            <TrendingUp size={20} class="text-primary" />
                            Equity Curve
                        </Card.Title>
                        <Card.Description class="text-xs">Cumulative PnL performance over the last 50 trades</Card.Description>
                    </Card.Header>
                    <Card.Content>
                        <div class="h-[300px] w-full">
                            <Chart.Container config={pnlConfig} class="h-full w-full">
                                <AreaChart 
                                    data={pnlData} 
                                    x="date" 
                                    y="pnl"
                                    axis="y"
                                    grid
                                />
                            </Chart.Container>
                        </div>
                    </Card.Content>
                </Card.Root>

                <Card.Root>
                    <Card.Header>
                        <Card.Title class="text-lg flex items-center gap-2">
                            <Award size={20} class="text-primary" />
                            Asset Breakdown
                        </Card.Title>
                        <Card.Description class="text-xs">PnL distribution per asset</Card.Description>
                    </Card.Header>
                    <Card.Content>
                        <div class="h-[300px] w-full">
                            <Chart.Container config={assetConfig} class="h-full w-full">
                                <BarChart 
                                    data={assetData} 
                                    x="symbol" 
                                    y="pnl"
                                    axis="y"
                                    grid
                                />
                            </Chart.Container>
                        </div>
                    </Card.Content>
                </Card.Root>
            </div>

            <!-- Table -->
            <Card.Root>
                <Card.Header class="flex flex-row items-center justify-between">
                    <div>
                        <Card.Title class="text-lg">Recent Trading Activity</Card.Title>
                        <Card.Description class="text-xs">The last 50 trades executed on Polymarket</Card.Description>
                    </div>
                    <Badge variant="outline" class="font-mono uppercase text-[10px] tracking-widest">Live Stream</Badge>
                </Card.Header>
                <Card.Content class="p-0">
                    <Table.Root>
                        <Table.Header>
                            <Table.Row>
                                <Table.Head class="w-[80px] pl-6 text-xs uppercase">ID</Table.Head>
                                <Table.Head class="text-xs uppercase text-sm">Time (UTC)</Table.Head>
                                <Table.Head class="text-xs uppercase">Market</Table.Head>
                                <Table.Head class="text-xs uppercase">Side</Table.Head>
                                <Table.Head class="text-xs uppercase">Edge</Table.Head>
                                <Table.Head class="text-xs uppercase">Price</Table.Head>
                                <Table.Head class="text-xs uppercase">PnL</Table.Head>
                                <Table.Head class="text-right pr-6 text-xs uppercase">Status</Table.Head>
                            </Table.Row>
                        </Table.Header>
                        <Table.Body>
                            {#each stats.recent_trades as trade}
                                <Table.Row class="text-sm">
                                    <Table.Cell class="font-mono text-[10px] text-muted-foreground pl-6">#{trade.id}</Table.Cell>
                                    <Table.Cell class="text-xs font-medium">
                                        {trade.timestamp.split('T')[1]?.slice(0, 8) || trade.timestamp}
                                    </Table.Cell>
                                    <Table.Cell class="font-bold text-slate-700">{trade.symbol}</Table.Cell>
                                    <Table.Cell>
                                        <Badge variant={trade.side === 'UP' ? 'secondary' : 'destructive'} class="rounded-sm font-black px-1.5 py-0 text-[10px]">
                                            {trade.side}
                                        </Badge>
                                    </Table.Cell>
                                    <Table.Cell class="font-mono text-xs text-muted-foreground">{(trade.edge * 100).toFixed(1)}%</Table.Cell>
                                    <Table.Cell class="font-mono text-xs">${trade.entry_price.toFixed(4)}</Table.Cell>
                                    <Table.Cell>
                                        {#if trade.settled}
                                            <span class="font-bold text-xs {trade.pnl_usd >= 0 ? 'text-emerald-600' : 'text-rose-600'}">
                                                ${trade.pnl_usd.toFixed(2)}
                                            </span>
                                        {:else}
                                            <span class="text-muted-foreground/30 text-xs">—</span>
                                        {/if}
                                    </Table.Cell>
                                    <Table.Cell class="text-right pr-6">
                                        {#if !trade.settled}
                                            <Badge class="bg-amber-500 hover:bg-amber-600 text-[9px] h-4 px-1.5 leading-none">LIVE</Badge>
                                        {:else if trade.final_outcome === 'STOP_LOSS'}
                                            <Badge variant="destructive" class="text-[9px] h-4 px-1.5 leading-none">STOP LOSS</Badge>
                                        {:else if trade.final_outcome === 'TAKE_PROFIT'}
                                            <Badge class="bg-emerald-500 hover:bg-emerald-600 text-[9px] h-4 px-1.5 leading-none">TAKE PROFIT</Badge>
                                        {:else if trade.exited_early}
                                            <Badge variant="outline" class="text-[9px] h-4 px-1.5 border-primary text-primary leading-none">REVERSED</Badge>
                                        {:else}
                                            <Badge variant="secondary" class="text-[9px] h-4 px-1.5 font-bold tracking-tighter leading-none">SETTLED</Badge>
                                        {/if}
                                    </Table.Cell>
                                </Table.Row>
                            {/each}
                        </Table.Body>
                    </Table.Root>
                </Card.Content>
            </Card.Root>
        {/if}
    </div>
</div>
