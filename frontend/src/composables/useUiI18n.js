import { useAppContext } from "./useAppContext";

export function useUiI18n() {
  const { locale, availableLocales, setLocale, t } = useAppContext();
  return { locale, availableLocales, setLocale, t };
}
