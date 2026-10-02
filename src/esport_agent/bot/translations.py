"""Texts of the Discord bot, in every supported language.

Discord sends the user's language with each command (`interaction.locale`): replies use it,
and the descriptions of the commands in Discord's menu are translated through
`CommandTranslator`. Languages other than French fall back to English.
"""

from typing import Literal

from discord import Locale
from discord.app_commands import TranslationContextTypes, Translator, locale_str

type Lang = Literal["fr", "en"]

TEXTS: dict[str, dict[Lang, str]] = {
    "roster_title": {"fr": "Roster de **{team}** :", "en": "Roster of **{team}**:"},
    "last_known_title": {
        "fr": "Dernier roster connu de **{team}** ({tournament}{ended}), plus actif :",
        "en": "Last known roster of **{team}** ({tournament}{ended}), no longer active:",
    },
    "ended": {"fr": ", terminé le {date}", "en": ", ended {date}"},
    "line": {"fr": "- {label} : {value}", "en": "- {label}: {value}"},
    "substitute": {"fr": " (remplaçant)", "en": " (sub)"},
    "staff_title": {"fr": "Staff :", "en": "Staff:"},
    "no_players": {
        "fr": "Aucun joueur connu pour **{team}**.",
        "en": "No known players for **{team}**.",
    },
    "several_starters": {
        "fr": "Plusieurs titulaires pour un même rôle : le roster peut inclure des remplaçants "
        "ou des joueurs inactifs.",
        "en": "Several starters for one role: the roster may include substitutes or inactive "
        "players.",
    },
    "missing_starters": {
        "fr": "Aucun titulaire en {roles} : le roster est peut-être incomplet.",
        "en": "No starter for {roles}: the roster may be incomplete.",
    },
    "next_match": {
        "fr": "Prochain match de **{team}** : vs {opponent}, {when} ({relative}) · {context}",
        "en": "Next match of **{team}**: vs {opponent}, {when} ({relative}) · {context}",
    },
    "live_match": {
        "fr": "**{team}** joue en ce moment contre {opponent} · {context}",
        "en": "**{team}** is playing {opponent} right now · {context}",
    },
    "no_next_match": {
        "fr": "Aucun match à venir pour **{team}**.",
        "en": "No upcoming match for **{team}**.",
    },
    "results_title": {
        "fr": "Derniers résultats de **{team}** :",
        "en": "Last results of **{team}**:",
    },
    "result_line": {
        "fr": "- {date} : {outcome} {score} vs {opponent} ({context})",
        "en": "- {date}: {outcome} {score} vs {opponent} ({context})",
    },
    "no_results": {"fr": "Aucun résultat pour **{team}**.", "en": "No results for **{team}**."},
    "unknown_team": {
        "fr": "Aucune équipe ne correspond à « {query} ».",
        "en": 'No team matches "{query}".',
    },
    "ambiguous_team": {
        "fr": "Plusieurs équipes correspondent à « {query} », précise laquelle :",
        "en": 'Several teams match "{query}", which one do you mean?',
    },
    "unknown_league": {
        "fr": "Aucune ligue suivie ne correspond à « {league} ».",
        "en": 'No tracked league matches "{league}".',
    },
    "not_in_league": {
        "fr": "Aucune équipe « {query} » ne joue en {league}.",
        "en": 'No "{query}" team plays in {league}.',
    },
    "not_allowed": {
        "fr": "Ce bot n'est pas disponible sur ce serveur.",
        "en": "This bot is not available on this server.",
    },
    "internal_error": {
        "fr": "Une erreur est survenue, réessaie plus tard.",
        "en": "Something went wrong, try again later.",
    },
}

COMMAND_TEXTS: dict[str, str] = {
    "Current roster of a team": "Roster actuel d'une équipe",
    "Next match of a team": "Prochain match d'une équipe",
    "Latest results of a team": "Derniers résultats d'une équipe",
    "Team name, short name or code (default team if empty)": (
        "Nom, nom court ou code de l'équipe (équipe par défaut si vide)"
    ),
    'League, to pick the team playing there (e.g. "LFL")': (
        "Ligue, pour choisir l'équipe qui y joue (par exemple « LFL »)"
    ),
    "Also show the staff (coaches, analysts, managers)": (
        "Afficher aussi le staff (coachs, analystes, managers)"
    ),
    "Number of results (1 to 10)": "Nombre de résultats (1 à 10)",
}
"""English descriptions of the slash commands (as written in `app.py`) -> French."""


def language(locale: Locale | str) -> Lang:
    """Return the reply language for a Discord locale: "fr" for French, "en" otherwise."""
    return "fr" if str(locale).lower().startswith("fr") else "en"


def text(key: str, lang: Lang, **values: object) -> str:
    return TEXTS[key][lang].format(**values)


class CommandTranslator(Translator):
    """Translates the descriptions of the slash commands shown in Discord's menu."""

    async def translate(
        self, string: locale_str, locale: Locale, context: TranslationContextTypes
    ) -> str | None:
        if language(locale) == "fr":
            return COMMAND_TEXTS.get(string.message)
        return None
