from studio.pages.base import Placeholder


class MyDecksPage(Placeholder):
    id, title, icon = "mydecks", "My decks", "mydecks"
    category = "Decks"
    description = "Your saved decks will appear here, ready to browse and organise."
