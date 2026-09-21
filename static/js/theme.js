/**
 * Appearance preferences: theme (dark / light) and density (comfortable / compact).
 *
 * Both are applied early in <head> (base.html) to avoid a flash of the wrong
 * appearance; this file wires up the toggle buttons, persists the choices, and
 * re-themes the charts. Theme and density deliberately live together — they are
 * the same concern (how the UI looks) and both need the same pre-paint hook.
 */
(function () {
    'use strict';
    const root = document.documentElement;
    const btn = document.getElementById('themeToggle');
    const densityBtn = document.getElementById('densityToggle');
    const KEY = 'tradevue-theme';
    const DENSITY_KEY = 'tradevue-density';

    function current() {
        return root.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    }

    function currentDensity() {
        return root.getAttribute('data-density') === 'compact' ? 'compact' : 'comfortable';
    }

    function apply(theme, persist) {
        root.setAttribute('data-theme', theme);
        if (persist !== false) {
            try { localStorage.setItem(KEY, theme); } catch (e) {}
        }
        if (btn) {
            btn.innerHTML = theme === 'dark'
                ? '<i class="bi bi-sun"></i>'
                : '<i class="bi bi-moon-stars"></i>';
        }
        // Sync the chart + RSI pane colors. The chart reads its palette from the
        // stylesheet, so this only needs to run AFTER the attribute has changed.
        if (window.app && window.app.chart && typeof window.app.chart.setTheme === 'function') {
            window.app.chart.setTheme(theme);
        }
    }

    /**
     * Compact trims control heights, card/cell padding and the chart panes.
     * No chart-specific work is needed: the ResizeObserver inside TradingChart
     * watches .chart-panel, whose height changes with the pane heights.
     */
    function applyDensity(density, persist) {
        const compact = density === 'compact';
        root.setAttribute('data-density', compact ? 'compact' : 'comfortable');
        if (persist !== false) {
            try { localStorage.setItem(DENSITY_KEY, compact ? 'compact' : 'comfortable'); } catch (e) {}
        }
        if (densityBtn) {
            // Show the ACTION, not the state: comfortable -> "collapse".
            densityBtn.innerHTML = compact
                ? '<i class="bi bi-arrows-expand"></i>'
                : '<i class="bi bi-arrows-collapse"></i>';
            densityBtn.title = compact
                ? 'Density: compact — switch to comfortable'
                : 'Density: comfortable — switch to compact';
        }
        // Density changes the chart pane heights, and the chart's own
        // ResizeObserver can take a moment to catch up — long enough for the
        // canvas to visibly overflow its pane. Force a re-measure here.
        //
        // NOTE: this MUST be timer-based, not requestAnimationFrame. rAF is
        // PAUSED in a background tab and this dashboard is exactly the kind of
        // page that gets left open in one — measured: an rAF-only version
        // silently never fired (0 calls) while the canvas stayed 442px inside a
        // 360px pane. Timers are throttled in background tabs but still run.
        const kickChart = () => {
            const chart = window.app && window.app.chart;
            if (chart && typeof chart.resize === 'function') chart.resize();
        };
        setTimeout(kickChart, 60);
        setTimeout(kickChart, 280);
        requestAnimationFrame(() => requestAnimationFrame(kickChart));
    }

    if (btn) {
        btn.addEventListener('click', () => {
            apply(current() === 'dark' ? 'light' : 'dark', true);
        });
    }

    if (densityBtn) {
        densityBtn.addEventListener('click', () => {
            applyDensity(currentDensity() === 'compact' ? 'comfortable' : 'compact', true);
        });
    }

    applyDensity(currentDensity(), false);   // set the initial icon + tooltip

    window.BotTraderX5Theme = {
        apply: apply,
        get: current,
        applyDensity: applyDensity,
        getDensity: currentDensity,
    };
})();
