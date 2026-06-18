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
            
            Chart.defaults.color = isDark ? '#94A3B8' : '#64748B';
            Chart.defaults.borderColor = isDark ? '#334155' : '#e2e8f0';
            Chart.defaults.backgroundColor = isDark ? '#1E293B' : '#ffffff';
            
            // Update existing charts
            Chart.instances.forEach(chart => {
                if (chart.options.scales) {
                    const gridColor = isDark ? '#334155' : '#e2e8f0';
                    const tickColor = isDark ? '#94A3B8' : '#64748B';
                    
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

// Initialize theme manager when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.themeManager = new ThemeManager();
});

// Export for modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ThemeManager;
}