import { useAppContext } from "./useAppContext";

// Client-side mirror of services/validators.py. Provides instant feedback;
// the server stays the authoritative gate. Keep the rules in sync with the
// backend module. Each validator returns "" when valid, or a localized
// error message when not.
export const RULES = {
  USERNAME_MIN: 3,
  USERNAME_MAX: 32,
  EMAIL_MAX: 254,
  PASSWORD_MIN: 8,
  PASSWORD_MAX: 128,
};

const USERNAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$/;
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function useValidators() {
  const { t } = useAppContext();

  function validateUsername(value) {
    const v = (value || "").trim();
    if (!v) return t("validation.usernameRequired");
    if (!USERNAME_RE.test(v)) {
      return t("validation.usernameFormat", {
        min: RULES.USERNAME_MIN,
        max: RULES.USERNAME_MAX,
      });
    }
    return "";
  }

  function validateEmail(value) {
    const v = (value || "").trim();
    if (!v) return t("validation.emailRequired");
    if (v.length > RULES.EMAIL_MAX || !EMAIL_RE.test(v)) {
      return t("validation.emailFormat");
    }
    return "";
  }

  function validatePassword(value) {
    const v = value || "";
    if (v.length < RULES.PASSWORD_MIN) {
      return t("validation.passwordMin", { min: RULES.PASSWORD_MIN });
    }
    if (v.length > RULES.PASSWORD_MAX) {
      return t("validation.passwordMax", { max: RULES.PASSWORD_MAX });
    }
    if (!/[A-Za-z]/.test(v) || !/\d/.test(v)) {
      return t("validation.passwordComplex");
    }
    return "";
  }

  return { validateUsername, validateEmail, validatePassword, RULES };
}
