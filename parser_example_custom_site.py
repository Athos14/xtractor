#!/usr/local/bin/python3.11
#coding: utf-8

"""
Exemple d'extension du système de parsing pour un nouveau site
Illustre comment créer une configuration et un parser personnalisés
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from bs4 import BeautifulSoup
import re

# Import depuis le fichier refactoré
from gdprhubRSS_refactored import ParserConfig, Parser, DecisionData, logger


# ============================================================================
# EXEMPLE 1 : Parser pour un site avec structure JSON
# ============================================================================

@dataclass
class JSONSiteParserConfig(ParserConfig):
    """Configuration pour parser un site retournant du JSON"""

    def __post_init__(self):
        self.name = "JSONSite"
        # Pas de tags HTML pour JSON
        super().__post_init__()

        # Mapping JSON → DecisionData
        self.json_mapping = {
            'decision_id': 'numero',
            'authority': 'juridiction',
            'country_code': 'pays',
            'decision_date': 'date',
            'fine_amount': 'quantum',
            'gdpr_articles': 'griefs',
            'source_url': 'URLsrc'
        }


class JSONSiteParser(Parser):
    """Parser pour un site JSON"""

    def can_parse(self, content: Any) -> bool:
        """Détecte si le contenu est du JSON"""
        if isinstance(content, dict):
            return 'decision_id' in content  # Marqueur spécifique
        return False

    def parse_content(self, summary: Any, src: str, artRGPD: List[str]) -> Optional[DecisionData]:
        """Parse le JSON"""
        try:
            decision = DecisionData()

            # Mapper les champs JSON
            for json_key, attr_name in self.config.json_mapping.items():
                if json_key in summary:
                    setattr(decision, attr_name, summary[json_key])

            # Articles RGPD
            if artRGPD:
                decision.griefs = ', '.join([f'"{grief}"' for grief in artRGPD])

            # Source
            if src:
                decision.URLsrc = src

            # Post-traitement
            decision = self._post_process_decision(decision)

            return decision

        except Exception as e:
            logger.error(f"❗ Erreur parsing JSON: {e}")
            return None

    def extract_references(self, summary: Any) -> List[str]:
        """Extrait les articles RGPD du JSON"""
        if isinstance(summary, dict) and 'gdpr_articles' in summary:
            return summary['gdpr_articles']
        return []


# ============================================================================
# EXEMPLE 2 : Parser pour un site avec structure table custom
# ============================================================================

@dataclass
class CustomTableParserConfig(ParserConfig):
    """Configuration pour un site avec des tables HTML personnalisées"""

    def __post_init__(self):
        self.name = "CustomTable"
        # Tags spécifiques au site
        self.content_tags = ("div", "decision-container")
        self.reference_tags = ("ul", "article-list")
        self.source_tags = ("a", "external-link")
        super().__post_init__()

    def _default_key_mapping(self) -> Dict[str, str]:
        """Mapping spécifique au site"""
        return {
            'Autorité compétente': 'juridiction',
            'État membre': 'pays',
            'Référence': 'numero',
            'Date de décision': 'date',
            'Montant': 'quantum',
            'Parties': 'nom',
            'Type de décision': 'type'
        }


class CustomTableParser(Parser):
    """Parser pour tables HTML personnalisées"""

    def can_parse(self, content: Any) -> bool:
        """Détecte la structure spécifique"""
        try:
            soup = BeautifulSoup(content, 'html.parser')
            tag_name, tag_class = self.config.content_tags
            return soup.find(tag_name, class_=tag_class) is not None
        except:
            return False

    def parse_content(self, summary: Any, src: str, artRGPD: List[str]) -> Optional[DecisionData]:
        """Parse la table custom"""
        try:
            soup = BeautifulSoup(summary, 'html.parser')
            tag_name, tag_class = self.config.content_tags
            container = soup.find(tag_name, class_=tag_class)

            if not container:
                return None

            decision = DecisionData()

            # Extraction via les <dt><dd> pairs
            for dt in container.find_all('dt'):
                dd = dt.find_next_sibling('dd')
                if dd:
                    key = dt.get_text(strip=True)
                    value = dd.get_text(strip=True)

                    if key in self.config.key_mapping:
                        attr_name = self.config.key_mapping[key]
                        setattr(decision, attr_name, value)

            # Articles RGPD
            if artRGPD:
                decision.griefs = ', '.join([f'"{grief}"' for grief in artRGPD])

            # Source
            if src:
                decision.URLsrc = src

            # Post-traitement
            decision = self._post_process_decision(decision)

            return decision

        except Exception as e:
            logger.error(f"❗ Erreur parsing CustomTable: {e}")
            return None

    def extract_references(self, summary: Any) -> List[str]:
        """Extrait les articles RGPD depuis une liste <ul>"""
        try:
            soup = BeautifulSoup(summary, 'html.parser')
            tag_name, tag_class = self.config.reference_tags
            ul = soup.find(tag_name, class_=tag_class)

            if not ul:
                return []

            articles = []
            for li in ul.find_all('li'):
                article_text = li.get_text(strip=True)
                # Nettoyer et formater
                article = article_text.replace("Article ", "").replace("GDPR", "RGPD")
                if article:
                    articles.append(article)

            return articles

        except Exception as e:
            logger.error(f"❗ Erreur extraction références CustomTable: {e}")
            return []


# ============================================================================
# EXEMPLE 3 : Parser avec regex complexes pour un format texte spécifique
# ============================================================================

@dataclass
class ComplexTextParserConfig(ParserConfig):
    """Configuration pour un format texte avec patterns complexes"""

    def __post_init__(self):
        self.name = "ComplexText"
        super().__post_init__()

        # Patterns regex avancés
        self.regex_patterns = {
            'decision_header': r'DECISION\s+N°\s*(\S+)\s+DU\s+(\d{2}/\d{2}/\d{4})',
            'authority': r'Autorité\s*:\s*([^\n]+)',
            'fine': r'Montant\s*:\s*€?\s*([\d\s,]+)',
            'articles': r'Articles?\s+(\d+(?:\.\d+)?(?:[a-z])?(?:\s*et\s*\d+(?:\.\d+)?(?:[a-z])?)*)',
            'parties': r'Parties?\s*:\s*([^\n]+)'
        }


class ComplexTextParser(Parser):
    """Parser pour texte avec patterns regex complexes"""

    def can_parse(self, content: Any) -> bool:
        """Détecte le format via pattern signature"""
        if not isinstance(content, str):
            return False
        pattern = self.config.regex_patterns.get('decision_header', '')
        return bool(re.search(pattern, content))

    def parse_content(self, summary: Any, src: str, artRGPD: List[str]) -> Optional[DecisionData]:
        """Parse avec regex"""
        try:
            decision = DecisionData()

            # Extraction numéro et date
            header_match = re.search(self.config.regex_patterns['decision_header'], summary)
            if header_match:
                decision.numero = header_match.group(1)
                decision.date = header_match.group(2)

            # Extraction autorité
            auth_match = re.search(self.config.regex_patterns['authority'], summary)
            if auth_match:
                decision.juridiction = auth_match.group(1).strip()

            # Extraction amende
            fine_match = re.search(self.config.regex_patterns['fine'], summary)
            if fine_match:
                decision.quantum = fine_match.group(1).replace(' ', '').replace(',', '')

            # Extraction parties
            parties_match = re.search(self.config.regex_patterns['parties'], summary)
            if parties_match:
                decision.nom = parties_match.group(1).strip()

            # Articles RGPD
            if artRGPD:
                decision.griefs = ', '.join([f'"{grief}"' for grief in artRGPD])

            # Source
            if src:
                decision.URLsrc = src

            # Post-traitement
            decision = self._post_process_decision(decision)

            return decision

        except Exception as e:
            logger.error(f"❗ Erreur parsing ComplexText: {e}")
            return None

    def extract_references(self, summary: Any) -> List[str]:
        """Extrait les articles avec regex"""
        try:
            pattern = self.config.regex_patterns.get('articles', '')
            matches = re.findall(pattern, summary)

            articles = []
            for match in matches:
                # Séparer les articles multiples (ex: "5 et 6")
                parts = re.split(r'\s+et\s+', match)
                for part in parts:
                    article = f"RGPD{part.strip()}"
                    articles.append(article)

            return articles

        except Exception as e:
            logger.error(f"❗ Erreur extraction références ComplexText: {e}")
            return []


# ============================================================================
# UTILISATION DES PARSERS PERSONNALISÉS
# ============================================================================

def demo_custom_parsers():
    """Démonstration d'utilisation des parsers personnalisés"""

    # Importer la factory
    from gdprhubRSS_refactored import ParserFactory

    # Créer une factory étendue
    class ExtendedParserFactory(ParserFactory):
        def __init__(self):
            super().__init__()
            # Ajouter les parsers personnalisés
            self.parsers.insert(0, JSONSiteParser(JSONSiteParserConfig()))
            self.parsers.insert(1, CustomTableParser(CustomTableParserConfig()))
            self.parsers.insert(2, ComplexTextParser(ComplexTextParserConfig()))

    # Utilisation
    factory = ExtendedParserFactory()

    # Exemple 1: JSON
    json_content = {
        'decision_id': '2024-001',
        'authority': 'CNIL',
        'country_code': 'FR',
        'decision_date': '15.03.2024',
        'fine_amount': '50000',
        'gdpr_articles': ['RGPD5', 'RGPD6'],
        'source_url': 'https://example.com/decision'
    }

    decision1 = factory.parse_with_auto_detection(json_content, "", [])
    if decision1:
        print(f"✅ Parser sélectionné: {decision1.parsing_strategy}")
        print(f"   Décision: {decision1.numero}, Autorité: {decision1.juridiction}")

    # Exemple 2: HTML Custom
    html_content = """
    <div class="decision-container">
        <dl>
            <dt>Autorité compétente</dt><dd>ANSSI</dd>
            <dt>État membre</dt><dd>France</dd>
            <dt>Référence</dt><dd>DEC-2024-042</dd>
            <dt>Date de décision</dt><dd>22.01.2024</dd>
            <dt>Montant</dt><dd>75000</dd>
        </dl>
    </div>
    <ul class="article-list">
        <li>Article 5 GDPR</li>
        <li>Article 32 GDPR</li>
    </ul>
    """

    refs2 = factory.extract_references_auto(html_content)
    decision2 = factory.parse_with_auto_detection(html_content, "", refs2)
    if decision2:
        print(f"✅ Parser sélectionné: {decision2.parsing_strategy}")
        print(f"   Articles: {decision2.griefs}")

    # Exemple 3: Texte complexe
    text_content = """
    DECISION N° 2024-SANC-003 DU 12/04/2024

    Autorité : Commission Nationale Informatique et Libertés

    Montant : € 100,000

    Parties : SociétéX vs CNIL

    Articles 5.1.a et 6.1 du RGPD
    """

    refs3 = factory.extract_references_auto(text_content)
    decision3 = factory.parse_with_auto_detection(text_content, "", refs3)
    if decision3:
        print(f"✅ Parser sélectionné: {decision3.parsing_strategy}")
        print(f"   Numéro: {decision3.numero}, Amende: {decision3.quantum}")


if __name__ == '__main__':
    print("=== Démonstration des parsers personnalisés ===\n")
    demo_custom_parsers()
