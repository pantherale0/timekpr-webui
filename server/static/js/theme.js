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
        // Update Chart.js default colors for theme
        if (typeof Chart !== 'undefined') {
            const isDark = this.currentTheme === 'dark';
            
            Chart.defaults.color = isDark ? '#cbd5e1' : '#475569';
            Chart.defaults.borderColor = isDark ? '#334155' : '#e2e8f0';
            Chart.defaults.backgroundColor = isDark ? '#1e293b' : '#ffffff';
            
            // Update existing charts
            Chart.instances.forEach(chart => {
                if (chart.options.scales) {
                    const gridColor = isDark ? '#334155' : '#e2e8f0';
                    const tickColor = isDark ? '#cbd5e1' : '#475569';
                    
                    ['x', 'y'].forEach(axis => {
                        if (chart.options.scales[axis]) {
                            if (chart.options.scales[axis].grid) {
                                chart.options.scales[axis].grid.color = gridColor;
                            }
                            if (chart.options.scales[axis].ticks) {
                                chart.options.scales[axis].ticks.color = tickColor;
                            }
                        }
                    });
                }
                chart.update();
            });
        }
    }

    // Get current theme colors for JavaScript use
    getThemeColors() {
        const isDark = this.currentTheme === 'dark';
        
        return {
            primary: isDark ? '#f8fafc' : '#0f172a',
            secondary: isDark ? '#cbd5e1' : '#475569',
            tertiary: isDark ? '#94a3b8' : '#64748b',
            accent: '#3b82f6',
            success: '#10b981',
            warning: '#f59e0b',
            danger: '#ef4444',
            info: '#06b6d4',
            background: isDark ? '#020617' : '#ffffff',
            surface: isDark ? '#1e293b' : '#f8fafc',
            border: isDark ? '#334155' : '#e2e8f0'
        };
    }
}

// Initialize theme manager when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.themeManager = new ThemeManager();
});

// Export for modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ThemeManager;
}