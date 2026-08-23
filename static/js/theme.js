/**
 * Theme toggle (dark / light).
 * The initial theme is applied early in <head> (base.html) to avoid FOUC;
 * this file wires up the toggle button, persists the choice, and syncs the
 * chart colors via TradingChart.setTheme().
 */
(function () {
    'use strict';
    const root = document.documentElement;
    const btn = document.getElementById('themeToggle');
    const KEY = 'tradevue-theme';

    function current() {
        return root.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
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
        // Sync the chart + RSI pane colors.
        if (window.app && window.app.chart && typeof window.app.chart.setTheme === 'function') {
            window.app.chart.setTheme(theme);
        }
    }

    if (btn) {
        btn.addEventListener('click', () => {
            apply(current() === 'dark' ? 'light' : 'dark', true);
        });
    }

    window.BotTraderX5Theme = {
        apply: apply,
        get: current,
    };
})();
