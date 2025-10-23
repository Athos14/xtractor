#!/usr/local/bin/python3.11
#coding: utf-8

'''
Script Name: gdprhubRSS.py
Author: @th0s
Date: 2025 10 22
Version: 3.0.a1 (Refactored with Factory Pattern)
Description: Extraction et enregistrement des données du feed RSS GDPRHub -
            Refactorisé avec design pattern Factory

            https://gdprhub.eu/index.php?title=Special:NewPages&feed=atom&hideredirs=1&limit=10&render=1
'''
import json
import locale
import os
import re
import requests
import time

from abc import ABC, abstractmethod
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Tuple, List, Dict

from myDLL.config import settings
from myDLL.furtif import defUserAgent
from myDLL.temps import dateActuelle
from myDLL.texte import saveMDFile
from myDLL.traduction import deeplTrans, translate_country, translate_sensDecision, translateAcronyme, translate_APD, acronymeAPD_translation
from myDLL.systeme import clean_filename, logger

BASE_DIR = Path(__file__).parent.resolve()

locale.setlocale(locale.LC_TIME, 'fr_FR.UTF-8')

entrées_traitées = []

killText = [
    'Share your comments here!',
    'Share blogs or news articles here!',
    'The decision below is a machine translation of the Italian original. Please refer to the Italian original for more details.'
]

logger = logger(verbose=True, fichierLog=Path(BASE_DIR, "gdprhub.logs"), nom_logger="GDPRHub Logs", console=True)
suivi_fichier = settings.Feeds.GDPRjson


# ============================================================================
# DATACLASSES - Données de décision
# ============================================================================

@dataclass
class DecisionData:
    """Classe représentant une décision RGPD"""
    id: str = ""
    juridiction: str = ""
    pays: str = ""
    numero: str = ""
    type: str = ""
    outcome: str = ""
    quantum: str = ""
    date: str = ""
    griefs: list = field(default_factory=list)
    URLsrc: str = ""
    nom: str = ""

    # Champs pour le post-traitement
    apd_traduite: str = ""
    date_convertie: str = ""
    date_titre: str = ""
    date_actuelle: str = ""
    champ: str = ""
    proposed_filename: str = ""

    # Contenu textuel
    texte_brut: str = ""
    texte_FR: str = ""

    # Métadonnées du script
    rss: bool = False
    parsing_strategy: str = "unknown"


# ============================================================================
# DATACLASSES - Configuration des parsers
# ============================================================================

@dataclass
class ParserConfig:
    """
    Classe mère pour la configuration des parsers.
    Définit les éléments HTML/XML à rechercher et la stratégie de mapping.
    """
    name: str
    # Tags à rechercher : (nom_tag, classe_css ou attribut)
    content_tags: Tuple[str, str] = ("table", "wikitable")
    reference_tags: Tuple[str, str] = ("table", "wikitable")
    source_tags: Tuple[str, str] = ("table", "wikitable")

    # Mapping des clés HTML vers les attributs DecisionData
    key_mapping: Dict[str, str] = field(default_factory=dict)

    # Patterns regex si nécessaire
    regex_patterns: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        """Initialisation par défaut du key_mapping si vide"""
        if not self.key_mapping:
            self.key_mapping = self._default_key_mapping()

    def _default_key_mapping(self) -> Dict[str, str]:
        """Mapping par défaut (peut être surchargé)"""
        return {}


@dataclass
class WikitableParserConfig(ParserConfig):
    """Configuration pour parser les tableaux Wikitable HTML"""

    def __post_init__(self):
        self.name = "Wikitable"
        self.content_tags = ("table", "wikitable")
        self.reference_tags = ("table", "wikitable")
        self.source_tags = ("table", "wikitable")
        super().__post_init__()

    def _default_key_mapping(self) -> Dict[str, str]:
        return {
            # Autorité/Juridiction
            'Authority': 'juridiction',
            'Court': 'juridiction',
            'DPA Abbrevation': 'juridiction',
            'Court Abbrevation': 'juridiction',

            # Autres métadonnées
            'Jurisdiction': 'pays',
            'Case Number/Name': 'numero',
            'National Case Number/Name': 'numero',
            'Type': 'type',
            'Outcome': 'outcome',
            'Decided': 'date',
            'Fine': 'quantum',
            'Parties': 'nom'
        }


@dataclass
class WikicodeParserConfig(ParserConfig):
    """Configuration pour parser le wikicode (format texte avec pipes)"""

    def __post_init__(self):
        self.name = "Wikicode"
        # Pas de tags HTML pour le wikicode, on utilise des regex
        super().__post_init__()

        # Patterns regex spécifiques au wikicode
        self.regex_patterns = {
            'field_pattern': r'\|\s*([^=]+)=\s*([^\|]+)',
            'article_pattern': r'\|GDPR_Article_\d+=(.*?)<br />'
        }

        # Box types pour wikicode
        self.box_mappings = {
            'CJEUdecisionBOX': {
                'Date_Decided': 'date',
                'Case_Number_Name': 'numero',
                'gdpr_articles': 'griefs',
                'Opinion_Link': 'ccl',
                'Judgement_Link': 'URLsrc'
            },
            'DPAdecisionBOX': {
                'Jurisdiction': 'pays',
                'DPA_Abbrevation': 'juridiction',
                'gdpr_articles': 'griefs',
                'Case_Number_Name': 'numero',
                'Original_Source_Link_1': 'URLsrc',
                'Type': 'typeoriginal',
                'Outcome': 'type',
                'Date_Decided': 'date',
                'Fine': 'quantum',
                'Party_Name_1': 'nom',
                'Party_Name_2': 'partie2',
                'Appeal_To_Case_Number_Name': 'appel'
            },
            'COURTdecisionBOX': {
                'Jurisdiction': 'pays',
                'Court_Abbrevation': 'juridiction',
                'Court_English_Name': 'juridEN',
                'gdpr_articles': 'griefs',
                'Case_Number_Name': 'numero',
                'Original_Source_Link_1': 'URLsrc',
                'Type': 'typeoriginal',
                'Outcome': 'type',
                'Date_Decided': 'date',
                'Fine': 'quantum',
                'Party_Name_1': 'nom',
                'Party_Name_2': 'partie2',
                'Appeal_To_Case_Number_Name': 'appel'
            }
        }


@dataclass
class ProseParserConfig(ParserConfig):
    """Configuration pour parser le contenu en prose (texte libre)"""

    def __post_init__(self):
        self.name = "Prose"
        super().__post_init__()

        # Patterns regex pour extraire des infos du texte libre
        self.regex_patterns = {
            'fine': r'€([\d,]+)',
            'date': r'On (\d{1,2} \w+ \d{4})',
            'authority': r'title.*?\((.+?)\)',  # Du title de l'entrée
        }


# ============================================================================
# CLASSES DE PARSERS (Design Pattern Strategy)
# ============================================================================

class Parser(ABC):
    """Classe abstraite pour les parsers"""

    def __init__(self, config: ParserConfig):
        self.config = config

    @abstractmethod
    def can_parse(self, content: Any) -> bool:
        """Vérifie si ce parser peut traiter le contenu"""
        pass

    @abstractmethod
    def parse_content(self, summary: Any, src: str, artRGPD: List[str]) -> Optional[DecisionData]:
        """Parse le contenu et retourne un objet DecisionData"""
        pass

    @abstractmethod
    def extract_references(self, summary_html: str) -> List[str]:
        """Extrait les références RGPD du contenu"""
        pass

    def _post_process_decision(self, decision: DecisionData) -> DecisionData:
        """Post-traitement commun à tous les parsers"""
        # Nettoyage juridiction
        if decision.juridiction and decision.juridiction != 'NON_DEFINI':
            decision.juridiction = re.sub(r'\s*\([^)]*\)$', '', decision.juridiction).strip()
        else:
            decision.juridiction = "NON_DEFINI"

        # Traduction pays
        if decision.pays:
            decision.pays = translate_country(decision.pays)

        # Traduction type
        decision.type = translate_sensDecision(decision.type)

        # Outcome
        decision.outcome = "amende" if decision.quantum and decision.quantum.strip().lower() not in ['', 'n/a'] else []

        # Quantum
        if decision.quantum:
            qt = decision.quantum.strip()
            qt_clean = qt.replace(',', '').replace(' ', '').replace('€', '')
            decision.quantum = qt_clean if qt_clean.isdigit() else ""

        # Dates
        if decision.date:
            decision.date_convertie = convertir_date_format_iso(decision.date)
            decision.date_titre = fdate(decision.date_convertie)
        else:
            decision.date_convertie = '1601-01-01'
            decision.date_titre = "1er janvier 1601"

        decision.date_actuelle = dateActuelle()
        decision.champ = 'sanctionCNIL' if acronymeAPD_translation.get(decision.juridiction, '') == "CNIL" else []
        decision.apd_traduite = translate_APD(decision.juridiction)

        # Nom
        idParties = decision.nom
        idParties = "" if idParties.lower() in ["n/a", ""] else idParties + ", "
        decision.nom = idParties

        # Filename
        juridAcro = translateAcronyme(decision.juridiction)
        decision.proposed_filename = f"{juridAcro}, {decision.date_titre}, n° {decision.numero}"

        decision.parsing_strategy = self.config.name

        return decision


class WikitableParser(Parser):
    """Parser pour les tableaux Wikitable HTML"""

    def can_parse(self, content: Any) -> bool:
        """Vérifie si le contenu contient une wikitable"""
        try:
            soup = BeautifulSoup(content, 'html.parser')
            tag_name, tag_class = self.config.content_tags
            return soup.find(tag_name, class_=tag_class) is not None
        except:
            return False

    def parse_content(self, summary: Any, src: str, artRGPD: List[str]) -> Optional[DecisionData]:
        """Parse un tableau Wikitable"""
        try:
            soup = BeautifulSoup(summary, 'html.parser')
            tag_name, tag_class = self.config.content_tags
            table = soup.find(tag_name, class_=tag_class)

            if not table:
                return None

            decision = DecisionData()

            # Parcourir la table
            for row in table.find_all('tr'):
                cells = row.find_all(['th', 'td'])
                if len(cells) == 2:
                    # Nettoyer la clé
                    key = cells[0].get_text(strip=True).replace(':', '')
                    value = cells[1].get_text(strip=True)

                    # Mapper vers DecisionData
                    if key in self.config.key_mapping:
                        attribute_name = self.config.key_mapping[key]
                        setattr(decision, attribute_name, value)

            # Ajouter les griefs
            if artRGPD:
                decision.griefs = ', '.join([f'"{grief}"' for grief in artRGPD])

            # Ajouter la source
            if src:
                decision.URLsrc = src

            # Post-traitement
            decision = self._post_process_decision(decision)

            return decision

        except Exception as e:
            logger.error(f"❗ Erreur parsing Wikitable: {e}")
            return None

    def extract_references(self, summary_html: str) -> List[str]:
        """Extrait les articles RGPD depuis le tableau HTML"""
        try:
            soup = BeautifulSoup(summary_html, 'html.parser')
            tag_name, tag_class = self.config.reference_tags
            table = soup.find(tag_name, class_=tag_class)

            if not table:
                return []

            articles_rgpd = []

            # Trouve la ligne contenant "Relevant Law"
            relevant_law_header = table.find(lambda tag: tag.name in ['td', 'th'] and 'Relevant Law' in tag.text)
            if relevant_law_header:
                articles_cell = relevant_law_header.find_next_sibling(['td', 'th'])
                if articles_cell:
                    for link in articles_cell.find_all('a'):
                        article_text = link.get_text(strip=True)
                        article_modifie = article_text.replace(" GDPR", "RGPD").replace("Article ", "").replace(" ", "")
                        if article_modifie:
                            articles_rgpd.append(article_modifie)

            return articles_rgpd

        except Exception as e:
            logger.error(f"❗ Erreur extraction références Wikitable: {e}")
            return []


class WikicodeParser(Parser):
    """Parser pour le wikicode (format texte)"""

    def can_parse(self, content: Any) -> bool:
        """Vérifie si le contenu est du wikicode"""
        if not isinstance(content, str):
            return False

        # Cherche les box types
        for box_type in self.config.box_mappings.keys():
            if box_type in content:
                return True
        return False

    def parse_content(self, summary: Any, src: str, artRGPD: List[str]) -> Optional[DecisionData]:
        """Parse le wikicode"""
        try:
            # Identifier le type de box
            box_dict = None
            box_type = None
            for bt in self.config.box_mappings.keys():
                if bt in summary:
                    box_dict = self.config.box_mappings[bt]
                    box_type = bt
                    break

            if not box_dict:
                return DecisionData()

            decision = DecisionData()

            # Extraire avec regex
            pattern = re.compile(self.config.regex_patterns['field_pattern'])
            matches = pattern.findall(summary)

            for key, value in matches:
                adjusted_key = key.strip()
                if adjusted_key in box_dict:
                    attr_name = box_dict[adjusted_key]
                    setattr(decision, attr_name, value.strip().replace('\n', ' ').strip())

            # Cas spécial CJUE
            if box_type == 'CJEUdecisionBOX' and (not decision.juridiction or decision.juridiction == ""):
                decision.juridiction = 'CJUE'

            # Ajouter les griefs
            if artRGPD:
                decision.griefs = ', '.join(artRGPD)

            # Ajouter la source
            if src:
                decision.URLsrc = src

            # Post-traitement
            decision = self._post_process_decision(decision)

            return decision

        except Exception as e:
            logger.error(f"❗ Erreur parsing Wikicode: {e}")
            return None

    def extract_references(self, summary_html: str) -> List[str]:
        """Extrait les articles RGPD du wikicode"""
        try:
            pattern = self.config.regex_patterns['article_pattern']
            matches = re.findall(pattern, summary_html)

            articles_rgpd = []
            for match in matches:
                article_modifie = match.replace("GDPR", "RGPD").replace(" ", "").replace("Article", "")
                if article_modifie:
                    articles_rgpd.append(article_modifie)

            return articles_rgpd

        except Exception as e:
            logger.error(f"❗ Erreur extraction références Wikicode: {e}")
            return []


class ProseParser(Parser):
    """Parser pour le contenu en prose (texte libre)"""

    def can_parse(self, content: Any) -> bool:
        """Le prose parser peut toujours essayer (fallback)"""
        return True

    def parse_content(self, summary: Any, src: str, artRGPD: List[str]) -> Optional[DecisionData]:
        """Parse le texte en prose"""
        try:
            decision = DecisionData()

            # Extraction avec regex
            if 'fine' in self.config.regex_patterns:
                fine_match = re.search(self.config.regex_patterns['fine'], summary)
                if fine_match:
                    decision.quantum = fine_match.group(1)

            if 'date' in self.config.regex_patterns:
                date_match = re.search(self.config.regex_patterns['date'], summary)
                if date_match:
                    decision.date = date_match.group(1)

            # Ajouter les griefs
            if artRGPD:
                decision.griefs = ', '.join([f'"{grief}"' for grief in artRGPD])

            # Ajouter la source
            if src:
                decision.URLsrc = src

            # Post-traitement
            decision = self._post_process_decision(decision)

            return decision

        except Exception as e:
            logger.error(f"❗ Erreur parsing Prose: {e}")
            return None

    def extract_references(self, summary_html: str) -> List[str]:
        """Extraction des références depuis le texte libre (à implémenter)"""
        # TODO: Implémenter l'extraction depuis le prose
        return []


# ============================================================================
# FACTORY PATTERN
# ============================================================================

class ParserFactory:
    """Factory pour créer et sélectionner le bon parser"""

    def __init__(self):
        self.parsers: List[Parser] = [
            WikitableParser(WikitableParserConfig()),
            WikicodeParser(WikicodeParserConfig()),
            ProseParser(ProseParserConfig())
        ]

    def get_parser(self, content: Any) -> Optional[Parser]:
        """
        Sélectionne automatiquement le bon parser selon le contenu.
        Essaie chaque parser dans l'ordre jusqu'à trouver un match.
        """
        for parser in self.parsers:
            if parser.can_parse(content):
                logger.info(f"💡 Parser sélectionné: {parser.config.name}")
                return parser

        logger.warning("⚠️ Aucun parser approprié trouvé")
        return None

    def parse_with_auto_detection(self, summary: Any, src: str = "", artRGPD: List[str] = None) -> Optional[DecisionData]:
        """
        Parse automatiquement avec détection du bon parser.

        :param summary: Contenu à parser
        :param src: URL source
        :param artRGPD: Liste des articles RGPD
        :return: DecisionData ou None
        """
        parser = self.get_parser(summary)
        if parser:
            return parser.parse_content(summary, src, artRGPD or [])
        return None

    def extract_references_auto(self, summary: Any) -> List[str]:
        """
        Extrait les références avec auto-détection du parser.

        :param summary: Contenu à parser
        :return: Liste des références RGPD
        """
        parser = self.get_parser(summary)
        if parser:
            return parser.extract_references(summary)
        return []


# ============================================================================
# FONCTIONS UTILITAIRES
# ============================================================================

def convertir_date_format_iso(date_string: str) -> str:
    '''
    Formatter une date au format DD.MM.YYYY en date ISO

    :param date_string: string contenant la date au format DD.MM.YYYY
    :return: string date au format ISO
    '''
    try:
        date_obj = datetime.strptime(date_string, '%d.%m.%Y')
        return date_obj.strftime('%Y-%m-%d')
    except ValueError:
        return "Format de date invalide"


def fdate(dateISO: str) -> str:
    '''
    Formatter une date ISO au format d MMMM YYYY

    :param dateISO: string contenant une date au format ISO
    :return: string contenant une date au format d MMMM YYYY
    '''
    date_obj = datetime.fromisoformat(dateISO)
    day = date_obj.day
    month = date_obj.strftime('%B')
    year = date_obj.year

    if day == 1:
        day_str = "1er"
    else:
        day_str = str(day).lstrip('0')

    formatted_date = f"{day_str} {month} {year}"
    return formatted_date


def est_traitée(identifiant: str) -> bool:
    '''
    Méthode vérifiant si ID déjà traité

    :param identifiant: str - identifiant de la publication
    :return: bool - présent dans la liste des entrées traitées
    '''
    global entrées_traitées
    return identifiant in entrées_traitées


def ajouter_entrée_traitée(identifiant: str) -> bool:
    '''
    Méthode ajoutant une entrée au fichier de suivi

    :param identifiant: str - identifiant de la décision vérifiée
    :return: bool - True si tout s'est bien passé
    '''
    entrées_traitées.append(identifiant)
    try:
        with open(suivi_fichier, 'w') as fichier:
            json.dump(entrées_traitées, fichier)
            return True
    except Exception as e:
        logger.error(f"❗ Erreur à l'ajout de {identifiant} dans le JSON: {e}")
        return False


def extract_url_src(summary_soup: BeautifulSoup) -> str:
    '''
    Extrait le lien vers l'URL source depuis le tableau HTML.

    :param summary_soup: BeautifulSoup - soup BS4 parsée
    :return: str - URL source
    '''
    table = summary_soup.find('table', class_='wikitable')
    if not table:
        return ""

    relevant_law_header = table.find(lambda tag: tag.name in ['td', 'th'] and 'Original Source' in tag.text)
    if relevant_law_header:
        articles_cell = relevant_law_header.find_next_sibling(['td', 'th'])
        if articles_cell:
            link = articles_cell.find('a')
            if link:
                return link.get("href", "")
    return ""


def formatgdprBox(contenu: DecisionData) -> str:
    '''
    Mise en forme des éléments extraits de GDPRHub sous forme de texte

    :param contenu: DecisionData contenant les éléments de la décision
    :return: Texte formaté en Markdown
    '''
    md_content = f"""---
aliases: []
creation: {contenu.date_actuelle}
griefs: [{contenu.griefs}]
pays: {contenu.pays}
juridiction: {contenu.apd_traduite or contenu.juridiction}
date: {contenu.date_convertie}
type: {contenu.type}
sanction: {contenu.outcome}
quantum: {contenu.quantum}
domaine: []
sanctionCtr: []
champ: {contenu.champ}
---
**Liens**:
**Autorité**: {contenu.juridiction}
**Sources**: [GDPRHub]({contenu.id}) ; [Original]({contenu.URLsrc})

---
```
{translateAcronyme(contenu.juridiction)}, {contenu.date_titre}, {contenu.nom}n° {contenu.numero}
```
---
#AI_intégrer

{contenu.texte_FR}
"""
    return md_content


def lire_flux_BS4(url: str, test_mode: bool = False) -> List[DecisionData]:
    '''
    Méthode lisant le flux RSS fourni et permettant un nettoyage du texte HTML

    :param url: str - URL du feed
    :param test_mode: bool - Mode test
    :return: list[DecisionData] - liste d'articles
    '''
    user_agent = defUserAgent()
    headers = {"User-Agent": user_agent}

    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.content, 'xml')

    entries = soup.find_all('entry')
    if test_mode:
        logger.debug(f"🔍🛠️ Nombre d'entrées trouvées: {len(entries)}")

    # Créer la factory
    factory = ParserFactory()

    articles = []

    for entry in entries:
        entry_id = entry.find('id').text
        summary_html = entry.find('summary').text
        summary_soup = BeautifulSoup(summary_html, 'html.parser')

        if test_mode or not est_traitée(entry_id):
            # Utilisation de la factory pour auto-détection
            artRGPD = factory.extract_references_auto(summary_html)
            src = extract_url_src(summary_soup)

            # Parsing avec auto-détection
            decision_data = factory.parse_with_auto_detection(
                summary=summary_html,
                src=src,
                artRGPD=artRGPD
            )

            if not decision_data:
                logger.warning(f"\n⚠️ Boxdata vide pour: {entry_id}\n")
                decision_data = DecisionData()
                decision_data.id = entry_id
                decision_data.griefs = 'Erreur'
                decision_data.proposed_filename = time.strftime(f"GDPRHub-%Y%m%d%H%M%S")

            # Extraction des sections Facts, Holding, Comment
            facts_heading = summary_soup.find('span', id='Facts')
            facts_content = ""
            if facts_heading:
                for sibling in facts_heading.find_parent('h3').find_next_siblings():
                    if sibling.name.startswith('h'):
                        break
                    if hasattr(sibling, 'text'):
                        facts_content += sibling.get_text(separator=' ', strip=True) + '\n'

            holding_heading = summary_soup.find('span', id="Holding")
            holding_content = ""
            if holding_heading:
                for sibling in holding_heading.find_parent('h3').find_next_siblings():
                    if sibling.name.startswith('h'):
                        break
                    if hasattr(sibling, 'text'):
                        holding_content += sibling.get_text(separator=' ', strip=True) + '\n'

            cmtr_heading = summary_soup.find('span', id="Comment")
            cmtr_content = ""
            if cmtr_heading:
                parent_heading = cmtr_heading.find_parent(['h2', 'h3'])
                if parent_heading:
                    for sibling in parent_heading.find_next_siblings():
                        if 'Share your comments here!' in sibling.get_text():
                            break
                        if sibling.name.startswith('h'):
                            break
                        if hasattr(sibling, 'text'):
                            cmtr_content += sibling.get_text(separator=' ', strip=True) + '\n'

            txt = '\n'.join([facts_content, "# Décision", holding_content, "# Commentaire", cmtr_content])
            txt_FR = deeplTrans(txt)

            decision_data.id = entry_id
            decision_data.texte_FR = txt_FR
            decision_data.rss = True

            articles.append(decision_data)
        else:
            logger.info(f"💡 Déjà traité: {entry_id}")

    return articles


def run(test_mode: bool = False) -> None:
    '''
    Analyse des actualités sur le flux RSS gdprHub

    :param test_mode: bool - [TEST MODE] activé
    :return: aucun retour attendu
    '''
    global entrées_traitées

    print('\n\n\n\t\t*** RSS GDPRHub (Refactored) ***\n')
    cabKM = settings.cab_km_dir
    rss_url = settings.Feeds.GDPRHub

    if os.path.exists(suivi_fichier) and not test_mode:
        try:
            with open(suivi_fichier, 'r') as fichier:
                entrées_traitées = json.load(fichier)
        except Exception as e:
            logger.error(f"❗ Erreur lors du chargement du JSON: {e}")
            entrées_traitées = []
    else:
        entrées_traitées = []

    articles = lire_flux_BS4(rss_url, True)
    if test_mode:
        logger.debug(f"\n🔍🛠️ Longueur articles: {len(articles)}\n")

    for article in articles:
        texte = formatgdprBox(article)
        try:
            logger.debug(f"🔍🛠️ Filename brut proposé: '{article.proposed_filename}'")
            clean_proposed_filename = clean_filename(article.proposed_filename)
        except Exception as e:
            logger.error(f"❗ Erreur {e} avec {article.id}\n")
            clean_proposed_filename = time.strftime(f"GDPRHub-%Y%m%d%H%M%S")

        nomFichier = cabKM / clean_proposed_filename
        d = {
            'proposed_filename': nomFichier,
            'md_content': texte,
            'rss': True
        }

        s = False
        try:
            logger.info(f"💡 Tentative d'enregistrement auto vers: {nomFichier}")
            s = saveMDFile(d)
        except Exception as e:
            logger.warning(f"⚠️ Échec de l'enregistrement auto. Fallback activé pour: {nomFichier}")

        if s and not test_mode:
            logger.info(f"💡 Enregistrement réussi, ajout au suivi: {article.id}")
            ajouter_entrée_traitée(article.id)
        elif not s and not test_mode:
            logger.error(f"❌ L'enregistrement a échoué pour {article.id}. L'article n'est PAS ajouté au suivi.")

    logger.info(f"💡 Fin du feed GDPRHub")


if __name__ == '__main__':
    if Path.cwd() != BASE_DIR:
        os.chdir(BASE_DIR)

    c = input("TEST MODE? O pour oui: ")
    DEBUG = False

    if c.lower() == "o":
        DEBUG = True
    run(test_mode=DEBUG)
