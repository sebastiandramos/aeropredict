"""Mapa de coordenadas aeropuertos ICAO → (lat, lon).

Todas las coordenadas son grados decimales WGS84.
"""

# (lat, lon) para todos los aeropuertos españoles + hubs europeos principales
AIRPORT_COORDS: dict[str, tuple[float, float]] = {
    # --- España peninsular ---
    "LEMD": (40.4719, -3.5626),  # Madrid-Barajas
    "LEBL": (41.2971, 2.0785),  # Barcelona-El Prat
    "LEAL": (38.2822, -0.5582),  # Alicante-Elche
    "LEMG": (36.6749, -4.4991),  # Málaga-Costa del Sol
    "LEVC": (39.4893, -0.4816),  # Valencia-Manises
    "LEIB": (38.8729, 1.3731),  # Ibiza
    "LEPA": (39.5517, 2.7387),  # Palma de Mallorca
    "LEZG": (41.6662, -1.0415),  # Zaragoza
    "LEAS": (43.5636, -6.0346),  # Asturias
    "LEVX": (42.2318, -8.6268),  # Vigo-Peinador
    "LESO": (43.3563, -1.7906),  # San Sebastián
    "LEBB": (43.3011, -2.9106),  # Bilbao
    "LEXJ": (43.4271, -3.8200),  # Santander
    "LECO": (43.3021, -8.3772),  # A Coruña
    "LEGE": (41.9010, 2.7605),  # Girona-Costa Brava
    "LELL": (41.5200, 2.1048),  # Sabadell
    "LELN": (42.5888, -5.6557),  # León
    "LEGR": (37.1887, -3.7772),  # Granada-José María Cordero
    "LEJR": (36.7446, -6.0601),  # Jerez
    "LEZL": (37.4180, -5.8931),  # Sevilla-San Pablo
    "LEBT": (37.8420, -4.8489),  # Córdoba
    "LEAB": (38.9485, -1.8632),  # Albacete
    "LEMO": (37.1749, -5.6159),  # Morón
    "LEMH": (39.8626, 4.2186),  # Menorca
    # --- Canarias ---
    "GCFV": (28.4527, -13.8638),  # Fuerteventura
    "GCLP": (27.9319, -15.3866),  # Gran Canaria
    "GCXO": (28.4827, -16.3415),  # Tenerife Norte
    "GCTS": (28.0445, -16.5725),  # Tenerife Sur
    "GCLA": (28.6260, -17.7556),  # La Palma
    "GCGM": (28.0296, -17.2146),  # La Gomera
    "GCHI": (27.8148, -17.8871),  # El Hierro
    "GCJA": (28.0486, -14.2403),  # Jandía
    # --- Portugal ---
    "LPPD": (37.7412, -25.6976),  # Ponta Delgada (Azores)
    "LPPT": (38.7756, -9.1354),  # Lisboa
    "LPPR": (41.2481, -8.6814),  # Porto
    "LPFR": (37.0141, -7.9657),  # Faro
    # --- UK / Ireland ---
    "EGLL": (51.4700, -0.4543),  # London Heathrow
    "EGKK": (51.1481, -0.1903),  # London Gatwick
    "EIDW": (53.4213, -6.2701),  # Dublin
    # --- Francia ---
    "LFPG": (49.0097, 2.5479),  # Paris CDG
    "LFPO": (48.7233, 2.3794),  # Paris Orly
    "LFLL": (45.7256, 5.0811),  # Lyon
    "LFMN": (43.6650, 7.2150),  # Niza
    # --- Alemania ---
    "EDDF": (50.0333, 8.5706),  # Frankfurt
    "EDDM": (48.3538, 11.7861),  # Múnich
    "EDDB": (52.3667, 13.5033),  # Berlín Brandenburg
    "EDDH": (53.6304, 9.9882),  # Hamburgo
    "EDDK": (50.8659, 7.1427),  # Colonia/Bonn
    # --- Benelux ---
    "EHAM": (52.3086, 4.7639),  # Ámsterdam Schiphol
    "EBBR": (50.9014, 4.4844),  # Bruselas
    # --- Italia ---
    "LIRF": (41.8003, 12.2389),  # Roma Fiumicino
    "LIML": (45.4451, 9.2773),  # Milán Linate
    "LIMC": (45.6300, 8.7231),  # Milán Malpensa
    # --- Suiza ---
    "LSZH": (47.4581, 8.5480),  # Zúrich
    "LSGG": (46.2381, 6.1094),  # Ginebra
    "LSZB": (46.9139, 7.4971),  # Berna
    # --- Austria ---
    "LOWW": (48.1103, 16.5697),  # Viena
    # --- Scandinavia ---
    "EKCH": (55.6180, 12.6560),  # Copenhague
    "ESSA": (59.6519, 17.9186),  # Estocolmo Arlanda
    "ENGM": (60.2020, 11.0839),  # Oslo Gardermoen
    "EFHK": (60.3183, 24.9633),  # Helsinki-Vantaa
    # --- Europa del Este ---
    "EPWA": (52.1657, 20.9671),  # Varsovia
    "LKPR": (50.1008, 14.2600),  # Praga
    "LHBP": (47.4297, 19.2611),  # Budapest
    "LROP": (44.5711, 26.0850),  # Bucarest Otopeni
    "LBSF": (42.6950, 23.4064),  # Sofía
    # --- Sur de Europa ---
    "LGAV": (37.9364, 23.9475),  # Atenas
    "LTFM": (41.2608, 28.7422),  # Estambul
    "LMML": (35.8575, 14.4775),  # Malta
    # --- Rusia ---
    "UUDD": (55.4100, 37.9061),  # Moscú Domodedovo
    "ULLI": (59.8004, 30.2625),  # San Petersburgo Pulkovo
}


def get_airport_coords(icao: str) -> tuple[float, float]:
    """Devuelve (lat, lon) para un código ICAO de aeropuerto.

    Raises:
        KeyError: Si el código no está en el mapa.
    """
    if icao not in AIRPORT_COORDS:
        msg = f"Aeropuerto desconocido: {icao}"
        raise KeyError(msg)
    return AIRPORT_COORDS[icao]
