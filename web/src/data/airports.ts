// Aeropuertos disponibles para la predicción.
// Fuente: AEROPUERTOS (src/aeropredict/opensky/config.py) + AIRPORT_COORDS
// (src/aeropredict/sources/airport_coords.py). Copiado como dataset estático TS.

export interface Airport {
  icao: string
  name: string
  city: string
  country: string
  lat: number
  lon: number
}

// (ICAO, nombre, ciudad, país, lat, lon)
const RAW: Array<[string, string, string, string, number, number]> = [
  // --- España peninsular ---
  ['LEMD', 'Adolfo Suárez Madrid-Barajas', 'Madrid', 'España', 40.4719, -3.5626],
  ['LEBL', 'Barcelona-El Prat', 'Barcelona', 'España', 41.2971, 2.0785],
  ['LEAL', 'Alicante-Elche', 'Alicante', 'España', 38.2822, -0.5582],
  ['LEMG', 'Málaga-Costa del Sol', 'Málaga', 'España', 36.6749, -4.4991],
  ['LEVC', 'Valencia-Manises', 'Valencia', 'España', 39.4893, -0.4816],
  ['LEIB', 'Ibiza', 'Ibiza', 'España', 38.8729, 1.3731],
  ['LEPA', 'Palma de Mallorca', 'Palma', 'España', 39.5517, 2.7387],
  ['LEZG', 'Zaragoza', 'Zaragoza', 'España', 41.6662, -1.0415],
  ['LEAS', 'Asturias', 'Asturias', 'España', 43.5636, -6.0346],
  ['LEVX', 'Vigo-Peinador', 'Vigo', 'España', 42.2318, -8.6268],
  ['LESO', 'San Sebastián', 'San Sebastián', 'España', 43.3563, -1.7906],
  ['LEBB', 'Bilbao', 'Bilbao', 'España', 43.3011, -2.9106],
  ['LEXJ', 'Santander-Seve Ballesteros', 'Santander', 'España', 43.4271, -3.82],
  ['LECO', 'A Coruña', 'A Coruña', 'España', 43.3021, -8.3772],
  ['LEGE', 'Girona-Costa Brava', 'Girona', 'España', 41.901, 2.7605],
  ['LELL', 'Sabadell', 'Sabadell', 'España', 41.52, 2.1048],
  ['LELN', 'León', 'León', 'España', 42.5888, -5.6557],
  ['LEGR', 'Granada-José María Cordero', 'Granada', 'España', 37.1887, -3.7772],
  ['LEJR', 'Jerez', 'Jerez', 'España', 36.7446, -6.0601],
  ['LEZL', 'Sevilla-San Pablo', 'Sevilla', 'España', 37.418, -5.8931],
  ['LEBT', 'Córdoba', 'Córdoba', 'España', 37.842, -4.8489],
  ['LEAB', 'Albacete', 'Albacete', 'España', 38.9485, -1.8632],
  ['LEMO', 'Morón', 'Morón', 'España', 37.1749, -5.6159],
  ['LEMH', 'Menorca', 'Menorca', 'España', 39.8626, 4.2186],
  // --- Canarias ---
  ['GCFV', 'Fuerteventura', 'Fuerteventura', 'España', 28.4527, -13.8638],
  ['GCLP', 'Gran Canaria', 'Gran Canaria', 'España', 27.9319, -15.3866],
  ['GCXO', 'Tenerife Norte-Ciudad de La Laguna', 'Tenerife', 'España', 28.4827, -16.3415],
  ['GCTS', 'Tenerife Sur', 'Tenerife', 'España', 28.0445, -16.5725],
  ['GCLA', 'La Palma', 'La Palma', 'España', 28.626, -17.7556],
  ['GCGM', 'La Gomera', 'La Gomera', 'España', 28.0296, -17.2146],
  ['GCHI', 'El Hierro', 'El Hierro', 'España', 27.8148, -17.8871],
  ['GCJA', 'Jandía', 'Jandía', 'España', 28.0486, -14.2403],
  // --- Portugal ---
  ['LPPD', 'Ponta Delgada-João Paulo II', 'Azores', 'Portugal', 37.7412, -25.6976],
  ['LPPT', 'Lisboa-Humberto Delgado', 'Lisboa', 'Portugal', 38.7756, -9.1354],
  ['LPPR', 'Porto-Francisco Sá Carneiro', 'Porto', 'Portugal', 41.2481, -8.6814],
  ['LPFR', 'Faro-Gago Coutinho', 'Faro', 'Portugal', 37.0141, -7.9657],
  // --- UK / Irlanda ---
  ['EGLL', 'London Heathrow', 'Londres', 'Reino Unido', 51.47, -0.4543],
  ['EGKK', 'London Gatwick', 'Londres', 'Reino Unido', 51.1481, -0.1903],
  ['EIDW', 'Dublin', 'Dublín', 'Irlanda', 53.4213, -6.2701],
  // --- Francia ---
  ['LFPG', 'Paris Charles de Gaulle', 'París', 'Francia', 49.0097, 2.5479],
  ['LFPO', 'Paris Orly', 'París', 'Francia', 48.7233, 2.3794],
  ['LFLL', 'Lyon', 'Lyon', 'Francia', 45.7256, 5.0811],
  ['LFMN', 'Niza', 'Niza', 'Francia', 43.665, 7.215],
  // --- Alemania ---
  ['EDDF', 'Frankfurt am Main', 'Fráncfort', 'Alemania', 50.0333, 8.5706],
  ['EDDM', 'Munich', 'Múnich', 'Alemania', 48.3538, 11.7861],
  ['EDDB', 'Berlin Brandenburg', 'Berlín', 'Alemania', 52.3667, 13.5033],
  ['EDDH', 'Hamburgo', 'Hamburgo', 'Alemania', 53.6304, 9.9882],
  ['EDDK', 'Colonia/Bonn', 'Colonia', 'Alemania', 50.8659, 7.1427],
  // --- Benelux ---
  ['EHAM', 'Amsterdam Schiphol', 'Ámsterdam', 'Países Bajos', 52.3086, 4.7639],
  ['EBBR', 'Bruselas', 'Bruselas', 'Bélgica', 50.9014, 4.4844],
  // --- Italia ---
  ['LIRF', 'Roma Fiumicino', 'Roma', 'Italia', 41.8003, 12.2389],
  ['LIML', 'Milán Linate', 'Milán', 'Italia', 45.4451, 9.2773],
  ['LIMC', 'Milán Malpensa', 'Milán', 'Italia', 45.63, 8.7231],
  // --- Suiza ---
  ['LSZH', 'Zürich', 'Zúrich', 'Suiza', 47.4581, 8.548],
  ['LSGG', 'Genève', 'Ginebra', 'Suiza', 46.2381, 6.1094],
  ['LSZB', 'Berna', 'Berna', 'Suiza', 46.9139, 7.4971],
  // --- Austria ---
  ['LOWW', 'Vienna International', 'Viena', 'Austria', 48.1103, 16.5697],
  // --- Escandinavia ---
  ['EKCH', 'Copenhague Kastrup', 'Copenhague', 'Dinamarca', 55.618, 12.656],
  ['ESSA', 'Stockholm Arlanda', 'Estocolmo', 'Suecia', 59.6519, 17.9186],
  ['ENGM', 'Oslo Gardermoen', 'Oslo', 'Noruega', 60.202, 11.0839],
  ['EFHK', 'Helsinki-Vantaa', 'Helsinki', 'Finlandia', 60.3183, 24.9633],
  // --- Europa del Este ---
  ['EPWA', 'Varsovia Chopin', 'Varsovia', 'Polonia', 52.1657, 20.9671],
  ['LKPR', 'Václav Havel Prague', 'Praga', 'República Checa', 50.1008, 14.26],
  ['LHBP', 'Budapest Liszt Ferenc', 'Budapest', 'Hungría', 47.4297, 19.2611],
  ['LROP', 'Henri Coandă Bucarest', 'Bucarest', 'Rumanía', 44.5711, 26.085],
  ['LBSF', 'Sofía', 'Sofía', 'Bulgaria', 42.695, 23.4064],
  // --- Sur de Europa ---
  ['LGAV', 'Atenas Eleftherios Venizelos', 'Atenas', 'Grecia', 37.9364, 23.9475],
  ['LTFM', 'Estambul', 'Estambul', 'Turquía', 41.2608, 28.7422],
  ['LMML', 'Malta', 'Malta', 'Malta', 35.8575, 14.4775],
  // --- Rusia ---
  ['UUDD', 'Moscú Domodedovo', 'Moscú', 'Rusia', 55.41, 37.9061],
  ['ULLI', 'San Petersburgo Pulkovo', 'San Petersburgo', 'Rusia', 59.8004, 30.2625],
]

export const AIRPORTS: Airport[] = RAW.map(([icao, name, city, country, lat, lon]) => ({
  icao,
  name,
  city,
  country,
  lat,
  lon,
}))

export const AIRPORT_BY_ICAO: Map<string, Airport> = new Map(
  AIRPORTS.map((a) => [a.icao, a]),
)

// Aerolíneas IATA comunes para el datalist.
export const COMMON_AIRLINES: string[] = [
  'IB',
  'VY',
  'UX',
  'FR',
  'U2',
  'BA',
  'LH',
  'AF',
  'KL',
  'LX',
  'AZ',
  'TP',
  'TK',
  'EI',
  'SK',
  'DY',
  'AY',
  'LO',
  'OS',
  'SN',
]
