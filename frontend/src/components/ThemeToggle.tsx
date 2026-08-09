import { useEffect, useState } from "react";

type ThemeName = "light" | "dark";

const THEME_STORAGE_KEY = "telegram_depiler_theme";

const themes: Array<{ name: ThemeName; label: string; icon: string }> = [
  { name: "light", label: "日间模式", icon: "☀" },
  { name: "dark", label: "夜间模式", icon: "☾" },
];

function getSavedTheme(): ThemeName {
  const savedTheme = localStorage.getItem(THEME_STORAGE_KEY);
  return themes.some((theme) => theme.name === savedTheme) ? (savedTheme as ThemeName) : "light";
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState<ThemeName>(getSavedTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  return (
    <div className="theme-switcher" role="group" aria-label="界面颜色主题">
      {themes.map((option) => (
        <button
          key={option.name}
          type="button"
          className={`theme-option${theme === option.name ? " is-active" : ""}`}
          aria-label={option.label}
          aria-pressed={theme === option.name}
          title={option.label}
          onClick={() => setTheme(option.name)}
        >
          {option.icon}
        </button>
      ))}
    </div>
  );
}
