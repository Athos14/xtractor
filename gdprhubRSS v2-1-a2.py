#!/usr/local/bin/python3.11
#coding: utf-8

'''
Script Name: gdprhubRSS.py
Author: @th0s
Date: 2025 10 22
Version: 2.1.a2
Description: Extraction et enregistrement des données du feed RSS GDPRHub - 

            https://gdprhub.eu/index.php?title=Special:NewPages&feed=atom&hideredirs=1&limit=10&render=1
'''
import json
import locale
import os
import re
import requests
import time

from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from myDLL.config import settings
from myDLL.furtif import defUserAgent
from myDLL.temps import dateActuelle
from myDLL.texte import saveMDFile
from myDLL.traduction import deeplTrans, translate_country, translate_sensDecision,translateAcronyme, translate_APD, acronymeAPD_translation
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

# suivi_fichier = read_key_from_yaml('suivi-entrees', 'General') # '/Users/dms/Documents/Pro/Scripts/extractR/suivi_entrées.json'
suivi_fichier = settings.Feeds.GDPRjson

@dataclass
class DecisionData:
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
    parsing_strategy: str = "unknown" # Pour savoir comment on l'a parsé

cjeuBox = {
        'Date_Decided': 'date',
        'Case_Number_Name': 'numero',
        'gdpr_articles': 'griefs',
        'Opinion_Link': 'ccl',
        'Judgement_Link': 'URLsrc'
    }
apdBox = {
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
    }
courtBox = {
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
box_types = {'CJEUdecisionBOX': cjeuBox, 'DPAdecisionBOX': apdBox, 'COURTdecisionBOX': courtBox}

def formatgdprBox(contenu: DecisionData) -> str:
    '''
    Mise en forme des éléments extraits de GDPRHub sous forme de texte

    :param contenu: dictionnaire contenant les éléments nécessaires au formattage de la décision
    :return: Retourne le texte formaté
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

def convertir_date_format_iso(date_string: str) -> datetime :
    '''
    Formatter une date au format DD.MM.YYYY en date ISO
    
    :param date_string: string contenant la date au format DD.MM.YYYY
    :return: objet datetime au format ISO
    '''
    # Analyser la date dans le format DD.MM.YYYY
    try:
        date_obj = datetime.strptime(date_string, '%d.%m.%Y')
    except ValueError:
        # Gérer le cas où la date n'est pas dans le format attendu
        return "Format de date invalide"

    # Convertir en format ISO 8601 (YYYY-MM-DD)
    return date_obj.strftime('%Y-%m-%d')

def fdate(dateISO: str) -> str:
    '''
    Formatter une date ISO au format d MMMM YYYY
    
    :param dateISO: string contenant une date au format ISO
    :return: string contenant une date au format d MMMM YYYY
    '''
    # Convertir la chaîne ISO en objet datetime
    date_obj = datetime.fromisoformat(dateISO)

    # Extraire le jour, le mois en lettres et l'année
    day = date_obj.day
    month = date_obj.strftime('%B')
    year = date_obj.year

    # Formatter le jour correctement
    if day == 1:
        day_str = "1er"
    else:
        day_str = str(day).lstrip('0')  # Enlève le zéro initial pour les jours de 2 à 9

    # Combiner les éléments en une chaîne formatée
    formatted_date = f"{day_str} {month} {year}"
    
    return formatted_date

def est_traitée(identifiant: str) -> str:
    '''
    Méthode vérifiant si ID déjà traité
    
    :param titre: str - titre de la publication
    :return: titre présent dans la liste des entrées traitées
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

def obtenir_references_textuelles(sommaire_HTML: BeautifulSoup, test_mode: bool=False) -> list:
    '''
    
    :param sommaire_HTML: BeautifulSoup - soupe BS4 au format HTML à passer à l'une ou l'autre des fonctions pour extraire les références textuelles
    :param test_mode: bool (facultatif) - Active [TEST MODE]
    :return: list | None - Liste des références ou None en cas d'erreur
    '''
    def extract_griefs_from_Wikitable_html(summary_soup: BeautifulSoup) -> list:
        '''
        Extrait les articles RGPD depuis le tableau HTML.
        '''
        table = summary_soup.find('table', class_='wikitable')
        if not table:
            logger.warning(f"⚠️ Wikitable non trouvé")
            return []

        articles_rgpd = []
        
        # Trouve la ligne contenant "Relevant Law"
        relevant_law_header = table.find(lambda tag: tag.name in ['td', 'th'] and 'Relevant Law' in tag.text)
        if relevant_law_header:
            # La cellule avec les articles est la cellule sœur
            articles_cell = relevant_law_header.find_next_sibling(['td', 'th'])
            if articles_cell:
                # Trouve tous les liens qui référencent un article
                for link in articles_cell.find_all('a'):
                    article_text = link.get_text(strip=True)
                    # Nettoyage pour avoir un format comme "RGPD-5-1-a"
                    # article_modifie = article_text.replace("GDPR", "RGPD").replace("Article", "").replace(" ", "-")
                    article_modifie = article_text.replace(" GDPR", "RGPD").replace("Article ", "").replace(" ", "")
                    if article_modifie:
                        articles_rgpd.append(article_modifie)
        return articles_rgpd

    def extract_griefs_from_Wikicode(texte: str) -> list:
        '''
        Récupération du texte de la page web et extraction des articles concernés par la décision
        
        :param texte: string contenant le texte à analyser
        :return: liste contenant les articles
        '''
        # Recherche des champs GDPR_Article_X dans le texte
        matches = re.findall(r'\|GDPR_Article_\d+=(.*?)<br />', texte)
        
        # Liste pour stocker les valeurs modifiées
        articles_rgpd = []
        
        for match in matches:
            # Remplacement de "GDPR" par "RGPD", suppression des espaces et des mots "Article"
            article_modifie = match.replace("GDPR", "RGPD").replace(" ", "").replace("Article", "")
            # Ajouter l'article modifié à la liste s'il n'est pas vide
            if article_modifie:
                articles_rgpd.append(article_modifie)
        
        #for article in articles_rgpd:
        #    input(f'réf trouvée : {article}')
        
        return articles_rgpd
  
    def extract_griefs_from_prose(texte: str, test_mode: bool=False) -> Any:
        '''
        
        :param texte: str - Texte parsé
        :param test_mode: bool (facultatif) - Active [TEST MODE]
        :return: 
        '''
        pass

    try:
        # Parsing de la soupe HTML pour tenter extraction Wikitable
        soupe_parsee = BeautifulSoup(sommaire_HTML, 'html.parser') # Crée un nouvel objet soup pour l'analyser
        return extract_griefs_from_Wikitable_html(summary_soup=soupe_parsee)
    
    except Exception as e:          # Echec du parsing Wikitable
        logger.warning(f"⚠️ Echec du parsing Wikitable: {e}")

        try:
            # Tentative de parsing Wikicode
            return extract_griefs_from_Wikicode(texte=sommaire_HTML)
        
        except Exception as e:      # Echec du parsing Wikicode
            logger.warning(f"⚠️ Echec du parsing Wikicode: {e}")
            try:
                # Todo parsing prose
                return extract_griefs_from_prose(sommaire_HTML)
            
            except Exception as e:  # Echec général du parsing
                logger.warning(f"⚠️ Echec du parsing prose: {e}")
                return None

    
    

def extract_url_src(summary_soup: BeautifulSoup) -> list:
    '''
    Extrait le lien vers l'URL source depuis le tableau HTML.

    :param summary_soup: BeautifulSoup - soup BS4 parsée en BS
    :return: list 
    '''
    table = summary_soup.find('table', class_='wikitable')
    if not table:
        logger.warning(f"⚠️ Wikitable non trouvé")
        return []
    
    # Trouve la ligne contenant "Relevant Law"
    relevant_law_header = table.find(lambda tag: tag.name in ['td', 'th'] and 'Original Source' in tag.text)
    if relevant_law_header:
        # La cellule avec les articles est la cellule sœur
        articles_cell = relevant_law_header.find_next_sibling(['td', 'th'])
        if articles_cell:
            link = articles_cell.find('a')
            return link["href"]
        else:
            return ""

def parser_contenu(summary, src: str, artRGPD: list= None, cleanHTML: bool=False, test_mode: bool=False) -> Any:
    '''
    
    :param : 
    :param test_mode: bool (facultatif) - Active [TEST MODE]
    :return: 
    '''
    pass

    def parser_Wikitable(summary_soup: BeautifulSoup, artRGPD: list, src: str, test_mode: bool=False) -> DecisionData:
        '''
        Extrait les métadonnées de manière simple et robuste, nettoie le nom de
        la juridiction, et retourne un dictionnaire complet.

        :param summary_soup: BeautifulSoup - soup BS4 parsée en BS
        :param artRGPD: list
        :param src: str
        :param test_mode: bool
        :return: DecisionData - classe DecisionData complétée ou non
        '''
        # ÉTAPE 1 : LE MAPPING CORRIGÉ (SANS LES ':')
        d = DecisionData()
        key_mapping = {
            # --- Cibles pour le nom de l'autorité (LES CLÉS SONT NETTOYÉES) ---
            'Authority': 'juridiction',
            'Court': 'juridiction', # Corrigé !
            'DPA Abbrevation': 'juridiction',
            'Court Abbrevation': 'juridiction',

            # --- Autres métadonnées ---
            'Jurisdiction': 'pays',
            'Case Number/Name': 'numero',
            'National Case Number/Name': 'numero',
            'Type': 'type',
            'Outcome': 'outcome',
            'Decided': 'date',
            'Fine': 'quantum',
            'Parties': 'nom'
        }


        table = summary_soup.find('table', class_='wikitable')
        if not table:
            return None

        # On parcourt la table pour remplir les données brutes.
        for row in table.find_all('tr'):
            cells = row.find_all(['th', 'td'])
            if len(cells) == 2:
                # On nettoie la clé D'ABORD
                key = cells[0].get_text(strip=True).replace(':', '')
                value = cells[1].get_text(strip=True)
                
                # On vérifie si la clé nettoyée est dans notre mapping
                if key in key_mapping:
                    attribute_name = key_mapping[key]
                    setattr(d, attribute_name, value)

        # ÉTAPE 2 : NETTOYAGE FINAL
        if d.juridiction and d.juridiction != 'NON_DEFINI':
            # On lit et on réassigne directement à l'attribut
            d.juridiction = re.sub(r'\s*\([^)]*\)$', '', d.juridiction).strip()
        else:
            logger.warning(f"⚠️ Juridiction non trouvée (vérifier le mapping).")
            d.juridiction = "NON_DEFINI" # S'assurer que la valeur par défaut est bien là

        # --- Le reste de votre logique de traitement (qui est déjà correcte) ---
        if artRGPD:
            d.griefs = ', '.join([f'"{grief}"' for grief in artRGPD])

        if src:
            d.URLsrc= src

        if d.pays:
            d.pays = translate_country(d.pays)

        d.type = translate_sensDecision(d.type)
        d.outcome = "amende" if d.quantum and d.quantum.strip().lower() not in ['', 'n/a'] else []
        
        qt = d.quantum.strip()
        qt_clean = qt.replace(',', '').replace(' ', '').replace('€', '')
        d.quantum = qt_clean if qt_clean.isdigit() else ""

        if d.date:
            d.date_convertie = convertir_date_format_iso(d.date)
            d.date_titre = fdate(d.date_convertie)
        else: 
            d.date_convertie = '1601-01-01'
            d.date_titre = "1er janvier 1601"

        d.date_actuelle = dateActuelle()
        d.champ = 'sanctionCNIL' if acronymeAPD_translation.get(d.juridiction, '') == "CNIL" else []
        d.apd_traduite = translate_APD(d.juridiction)

        idParties = d.nom
        idParties = "" if idParties.lower() in ["n/a", ""] else idParties + ", "
        d.nom = idParties
        
        juridAcro = translateAcronyme(d.juridiction)

        d.proposed_filename = f"{juridAcro}, {d.date_titre}, n° {d.numero}"
        
        return d

    def parser_Wikicode_regex(summary, artRGPD, cleanHTML=False, test_mode: bool=False) -> dict:
        '''
        Extraction, sur le site internet, du tableau contenant les métadonnées
        
        :param summary: résumé de la décision
        :param artRGPD: liste des articles concernés par la décision
        :param cleanHTML: Facultatif, détermine s'il faut nettoyer le texte
        :param test_mode: bool (facultatif) - Active [TEST MODE]
        :return: dictionnaire avec le contenu du tableau mappé
        '''
        # Identifier le type de box
        for box_type in box_types:
            if box_type in summary:
                box_dict = box_types[box_type]
                break
        else:
            # return None  # Si aucun type de box n'est trouvé, retourner None
            return DecisionData() # Si aucun type de box n'est trouvé, renvoyer une DecisionData vierge

        box_data = {}
        d = DecisionData()

        pattern = re.compile(r'\|\s*([^=]+)=\s*([^\|]+)')
        matches = pattern.findall(summary)

        for key, value in matches:
            adjusted_key = key.strip()
            if adjusted_key in box_dict:
                box_data[box_dict[adjusted_key]] = value.strip().replace('\n', ' ').strip()
        
        if box_type == 'CJEUdecisionBOX' and ('juridiction' not in box_data or not box_data['juridiction']):
            # box_data['juridiction'] = 'CJUE'
            d.juridiction = 'CJUE'
        
        if artRGPD:
            # box_data['griefs'] = artRGPD 
            # box_data['griefs'] = ', '.join([f'"{grief}"' for grief in box_data.get('griefs', [])])
            d.griefs = ', '.join(artRGPD)
        
        if 'pays' in box_data:
            try:
                # box_data['pays'] = translate_country(box_data['pays'])
                d.pays=translate_country(d.pays)
            except Exception as e:
                logger.error(f"❗ Erreur translate_country: {e}")

        # Ajouter ici le nom de fichier proposé sans extension, avec traduction éventuelle
        # box_data['type'] = translate_sensDecision(box_data['type']) if box_data.get('type') else ''
        d.type = translate_sensDecision(d.type) if d.type else ''
        
        

        try:
            qt = d.quantum.strip()
            qt_clean = qt.replace(',', '').replace(' ', '')
            d.quantum = qt_clean
        except Exception as e:
            logger.error(f"❗ Erreur formatage quantum: {e}")

        # box_data['sanction'] = "amende" if box_data.get('quantum') and box_data['quantum'].strip() != '' else []
        d.outcome = 'amende' if d.quantum and d.quantum.strip() != '' else []

        # if 'date' in box_data and box_data['date']:
        if d.date:
            # box_data['date_convertie'] = convertir_date_format_iso(box_data['date'])
            d.date_convertie = convertir_date_format_iso(d.date)
        else:
            d.date_convertie = '1601-01-01'

        d.date_actuelle = dateActuelle()
        d.champ = 'sanctionCNIL' if acronymeAPD_translation.get(d.juridiction, d.juridiction) == "CNIL" else []
        d.apd_traduite = translate_APD(d.juridiction)
        
        idParties = d.nom
        if idParties == "n/a" or idParties == "" :
            idParties = ""
        else:
            idParties += ", "

        d.date_titre = fdate(d.date_convertie)

        juridAcro = d.juridiction
        juridAcro = translateAcronyme(juridAcro)

        d.proposed_filename = ''.join([juridAcro, ', ', d.date_titre, ', ', idParties, 'n° ', d.numero])

        return d

    def parser_prose(test_mode: bool=False) -> Any:
        '''
        
        :param : 
        :param test_mode: bool (facultatif) - Active [TEST MODE]
        :return: 

        Autorité : On peut la prendre du <title> de l'entrée : AP (The Netherlands).

        Amende : On cherche r"€([\d,]+)" -> €2,700,000.

        Date : On cherche r"On (\d{1,2} \w+ \d{4})" -> 6 December 2023.

        Numéro : Quasi impossible à trouver. On devra peut-être se contenter du nom (Experian Nederland B.V.) comme numero.

        '''
        pass



    try:
        # Parsing de la soupe HTML pour tenter extraction Wikitable
        soupe_parsee = BeautifulSoup(summary, 'html.parser') # Crée un nouvel objet soup pour l'analyser
        return parser_Wikitable(soupe_parsee, artRGPD, src)
    
    except Exception as e:          # Echec du parsing Wikitable
        logger.warning(f"⚠️ Echec du parsing Wikitable: {e}")

        try:
            # Tentative de parsing Wikicode
            return parser_Wikicode_regex(summary, artRGPD, cleanHTML)
        
        except Exception as e:      # Echec du parsing Wikicode
            logger.warning(f"⚠️ Echec du parsing Wikicode: {e}")
            try:
                # Todo parsing prose
                return parser_prose()
            
            except Exception as e:  # Echec général du parsing
                logger.warning(f"⚠️ Echec du parsing prose: {e}")
                return None

    


def lire_flux_BS4(url, test_mode: bool=False) -> list[DecisionData]:
    '''
    Méthode lisant le flux RSS fourni et permettant un nettoyage du texte HTML
    
    :param url: str - URL du feed
    :return: list[DecisionData] - liste d'articles
    '''
    user_agent = defUserAgent()
    headers = {
        "User-Agent": user_agent
    }

    response = requests.get(url, headers=headers)

    soup = BeautifulSoup(response.content, 'xml')

    entries = soup.find_all('entry')
    if test_mode:
        logger.debug(f"🔍🛠️ Nombre d'entrées trouvées avec BeautifulSoup: {len(entries)}")

    articles = []

    for entry in entries:
        entry_id = entry.find('id').text
        summary_html = entry.find('summary').text # Récupère le contenu HTML de la balise summary
        summary_soup = BeautifulSoup(summary_html, 'html.parser') # Crée un nouvel objet soup pour l'analyser


        if test_mode or not est_traitée(entry_id):
            # artRGPD = extract_griefs_from_Wikitable_html(summary_soup)
            artRGPD = obtenir_references_textuelles(summary_html)
            src = extract_url_src(summary_soup)
        
            # decision_data = parser_Wikitable(summary_soup, artRGPD, src)
            decision_data = parser_contenu(
                summary=summary_html, 
                src=src, 
                artRGPD=artRGPD, 
                cleanHTML=False, 
                test_mode=False)

            if not decision_data:
                    #input('pause car box - none') # Commenté le 26 06 24 car les none proviennent de pb sur le site
                    logger.warning(f"\n⚠️ Boxdata vide pour: {entry_id}\n")
                    decision_data = DecisionData()
                    decision_data.id= entry_id
                    decision_data.griefs = 'Erreur' # Ajout 26 06 24 pour tracer les fiches problématiques
                    decision_data.proposed_filename = time.strftime(f"GDPRHub-%Y%m%d%H%M%S")

            facts_heading = summary_soup.find('span', id='Facts')
            facts_content = ""
            if facts_heading:
                # Itérer sur les éléments frères qui suivent le titre
                for sibling in facts_heading.find_parent('h3').find_next_siblings():
                    if sibling.name.startswith('h'): # Arrêter au prochain titre
                        break
                    if hasattr(sibling, 'text'):
                        facts_content += sibling.get_text(separator=' ', strip=True) + '\n'

            holding_heading = summary_soup.find('span', id="Holding")
            holding_content = ""
            if holding_heading:
                for sibling in holding_heading.find_parent('h3').find_next_siblings():
                    if sibling.name.startswith('h'): # Arrêter au prochain titre
                        break
                    if hasattr(sibling, 'text'):
                        holding_content += sibling.get_text(separator=' ', strip=True) + '\n'

            cmtr_heading = summary_soup.find('span', id="Comment")
            cmtr_content = ""
            if cmtr_heading:
                # On cherche le parent, qui peut être h2 ou h3 (plus flexible)
                parent_heading = cmtr_heading.find_parent(['h2', 'h3'])
                
                # On vérifie que le parent a bien été trouvé AVANT de continuer
                if parent_heading:
                    for sibling in parent_heading.find_next_siblings():
                        if 'Share your comments here!' in sibling.get_text():   # Rubrique vierge, on passe
                            break
                        if sibling.name.startswith('h'):                        # Arrêter au prochain titre
                            break
                        if hasattr(sibling, 'text'):
                            cmtr_content += sibling.get_text(separator=' ', strip=True) + '\n'

            txt = '\n'.join([facts_content, "# Décision", holding_content, "# Commentaire", cmtr_content])
            
            txt_FR = deeplTrans(txt)

            decision_data.id = entry_id
            decision_data.texte_FR = txt_FR
            decision_data.rss = True

            articles.append(decision_data)
            # ajouter_entrée_traitée(entry_id) # Déplacé à après l'enregistrement du MD
        else:
            logger.info(f"💡 Déjà traité: {entry_id}")

    return articles

def run(test_mode: bool=False) -> None:
    '''
    Analyse des actualités sur le flux RSS gdprHub

    :param test_mode: bool - [TEST MODE] activé
    :return: aucun retour attendu
    '''
    global entrées_traitées
    
    print('\n\n\n\t\t*** RSS GDPRHub ***\n')
    # rss_url = read_key_from_yaml('gdprhub', 'RSS')
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
        logger.debug(f"\n🔍🛠️ longeur articles: {len(articles)}\n")

    for article in articles:
        texte = formatgdprBox(article)
        try:
            logger.debug(f"🔍🛠️ Filename brut proposé: '{article.proposed_filename}'")
            clean_proposed_filename = clean_filename(article.proposed_filename)
        except Exception as e:
            logger.error(f"❗ Erreur {e} avec {article.id}\n")
            clean_proposed_filename = time.strftime(f"GDPRHub-%Y%m%d%H%M%S")
        # nomFichier = ''.join([str(cabKM), '/', clean_proposed_filename])
        nomFichier = cabKM / clean_proposed_filename
        d = {
            'proposed_filename' : nomFichier,
            'md_content' : texte,
            'rss' : True
        }

        
        # if saveMDFile(d) and not test_mode:
        #     ajouter_entrée_traitée(article.id)      # Si le fichier a été sauvegardé, ajout de l'entrée à la liste de suivi

        s = False

        try:
            logger.info(f"💡 Tentative d'enregistrement auto vers: {nomFichier}")
            s = saveMDFile(d)
               
        except Exception as e:
            logger.warning(f"⚠️ Échec de l'enregistrement auto (ou mode test). Fallback activé pour: {nomFichier}")
        
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
    
    if c. lower() == "o":
        DEBUG=True
    run(test_mode=DEBUG)

