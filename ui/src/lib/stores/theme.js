import { writable } from 'svelte/store';
import { browser } from '$app/environment';

function createThemeStore() {
    // Initialize with system preference or fallback to 'light'
    const getInitialTheme = () => {
        if (!browser) return 'light';
        
        // Check localStorage first
        const stored = localStorage.getItem('theme');
        if (stored === 'dark' || stored === 'light') {
            return stored;
        }
        
        // Fall back to system preference
        if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
            return 'dark';
        }
        
        return 'light';
    };

    const { subscribe, set, update } = writable(getInitialTheme());

    return {
        subscribe,
        toggle: () => {
            update(current => {
                const newTheme = current === 'dark' ? 'light' : 'dark';
                if (browser) {
                    localStorage.setItem('theme', newTheme);
                    // Update the document class
                    if (newTheme === 'dark') {
                        document.documentElement.classList.add('dark');
                    } else {
                        document.documentElement.classList.remove('dark');
                    }
                }
                return newTheme;
            });
        },
        set: (value) => {
            if (browser) {
                localStorage.setItem('theme', value);
                // Update the document class
                if (value === 'dark') {
                    document.documentElement.classList.add('dark');
                } else {
                    document.documentElement.classList.remove('dark');
                }
            }
            set(value);
        },
        init: () => {
            if (browser) {
                const theme = getInitialTheme();
                if (theme === 'dark') {
                    document.documentElement.classList.add('dark');
                } else {
                    document.documentElement.classList.remove('dark');
                }
                set(theme);
            }
        }
    };
}

export const theme = createThemeStore();
