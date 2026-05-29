import { useAppContext } from "./useAppContext";

export function useTheme() {
  const { theme, themes, setTheme, toggleTheme } = useAppContext();
  return { theme, themes, setTheme, toggleTheme };
}
