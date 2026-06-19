/**
 * Theme Sync System
 * Automatically synchronizes the application theme with the browser/system preference.
 */

class ThemeManager {
    constructor() {
        this.init();
    }

    init() {
        // Query system preference
        this.systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)');
        
        // Initial apply
        this.updateTheme();
        
        // Listen for system theme changes dynamically
        this.systemPrefersDark.addEventListener('change', () => {
            this.updateTheme();
        });
    }

    updateTheme() {
        const theme = this.systemPrefersDark.matches ? 'dark' : 'light';
        document.documentElement.setAttribute('data-theme', theme);
        document.documentElement.setAttribute('data-bs-theme', theme);
        this.currentTheme = theme;
        this.updateChartTheme();
    }

    updateChartTheme() {
        if (typeof Chart === 'undefined') {
            return;
        }

        try {
            const isDark = this.currentTheme === 'dark';

            Chart.defaults.color = isDark ? '#94A3B8' : '#64748B';
            Chart.defaults.borderColor = isDark ? '#334155' : '#e2e8f0';
            Chart.defaults.backgroundColor = isDark ? '#1E293B' : '#ffffff';

            const charts = this._collectCharts();
            charts.forEach((chart) => {
                if (!chart.options?.scales) {
                    chart.update();
                    return;
                }

                const gridColor = isDark ? '#334155' : '#e2e8f0';
                const tickColor = isDark ? '#94A3B8' : '#64748B';

                ['x', 'y'].forEach((axis) => {
                    const scale = chart.options.scales[axis];
                    if (!scale) return;
                    if (scale.grid) {
                        scale.grid.color = gridColor;
                    }
                    if (scale.ticks) {
                        scale.ticks.color = tickColor;
                    }
                });
                chart.update();
            });
        } catch (err) {
            console.warn('Chart theme sync skipped:', err);
        }
    }

    _collectCharts() {
        const charts = [];
        const seen = new Set();

        const addChart = (chart) => {
            if (!chart || typeof chart.update !== 'function' || seen.has(chart)) {
                return;
            }
            seen.add(chart);
            charts.push(chart);
        };

        // Chart.js 3.x stores instances in a plain object map — never call .forEach on it.
        const instances = Chart.instances;
        if (instances && typeof instances === 'object') {
            Object.values(instances).forEach(addChart);
        }

        // Chart.js 4.x registry API
        const registry = Chart.registry;
        if (registry && typeof registry.getAll === 'function') {
            registry.getAll().forEach(addChart);
        }

        Object.values(window._chartInstances || {}).forEach(addChart);

        if (typeof Chart.getChart === 'function') {
            document.querySelectorAll('canvas').forEach((canvas) => {
                addChart(Chart.getChart(canvas));
            });
        }

        return charts;
    }

    // Get current theme colors for JavaScript use
    getThemeColors() {
        const isDark = this.currentTheme === 'dark';
        
        return {
            primary: isDark ? '#F1F5F9' : '#1E293B',
            secondary: isDark ? '#94A3B8' : '#64748B',
            tertiary: isDark ? '#64748B' : '#94A3B8',
            accent: '#4A6B5D',
            success: '#10b981',
            warning: '#b8956a',
            danger: '#8b6f63',
            info: '#5d8272',
            background: isDark ? '#141a21' : '#FBFBF9',
            surface: isDark ? '#1E293B' : '#f4f6f5',
            border: isDark ? '#334155' : '#e2e8f0'
        };
    }
}

// Initialize theme manager when DOM is loaded (singleton — survives SPA navigations)
document.addEventListener('DOMContentLoaded', () => {
    if (!window.themeManager) {
        window.themeManager = new ThemeManager();
    }
});

// Export for modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ThemeManager;
}