"""Kinyarwanda and French versions of texts written in English in the page templates and forms.

Templates use {% t "English text" %}. Lookup is by the exact English text; anything not listed here is
shown in English, so a missing translation never leaves a blank.

To correct a translation, change the text on the right-hand side only. Keep {placeholders} as they are.
Kinyarwanda texts should be reviewed by a native speaker.
"""

TEXT = {
    'rw': {
        # --- Shared page parts and hidden labels for screen readers ---
        'Open menu': 'Fungura menu',
        'Main navigation': 'Menu nyamukuru',
        'Popular medicine searches': 'Imiti ishakishwa cyane',
        'Filters': 'Kuyungurura',
        'Map of pharmacies with this medicine': 'Ikarita ya farumasi zifite uyu muti',
        'MedFinder in numbers': 'MedFinder mu mibare',
        'Service highlights': 'Ibyiza bya serivisi',
        'Pharmacy partner access': 'Kwinjira kwa farumasi',
        'Breadcrumb': 'Aho uri',
        'Pages': 'Amapaji',
        'Pickup code': 'Kode yo gufata umuti',
        'District': 'Akarere',
        '(optional)': '(si ngombwa)',
        'Pharmacy': 'Farumasi',
        'Address': 'Aderesi',
        'Reserved': 'Igihe wabikishije',
        'Rating': 'Amanota',
        'Reserve': 'Bikisha',

        # --- Form fields (labels, hints, errors) ---
        'Username': 'Izina ukoresha winjira',
        'Email': 'Imeyili',
        'Password': 'Ijambo ry’ibanga',
        'Confirm password': 'Ongera wandike ijambo ry’ibanga',
        'Pharmacy name': 'Izina rya farumasi',
        'License number': 'Nimero y’uruhushya',
        'License document': 'Inyandiko y’uruhushya',
        'Phone': 'Telefoni',
        'Latitude': 'Latitude (ubugari)',
        'Longitude': 'Longitude (uburebure)',
        'Choose district…': 'Hitamo akarere…',
        'Used to log in.': 'Rikoreshwa winjira.',
        'Used to reset your password if you forget it.': 'Ikoreshwa guhindura ijambo ry’ibanga niba waryibagiwe.',
        'At least 8 characters, not only numbers.': 'Nibura inyuguti 8, kandi atari imibare gusa.',
        'As issued by Rwanda FDA.': 'Nk’uko yatanzwe na Rwanda FDA.',
        'Photo or scan of your Rwanda FDA license: PDF, JPG or PNG, max 5 MB.':
            'Ifoto cyangwa kopi y’uruhushya rwa Rwanda FDA: PDF, JPG cyangwa PNG, ntirenze MB 5.',
        'Street, landmark or building (optional).': 'Umuhanda, ahantu hazwi cyangwa inyubako (si ngombwa).',
        'Patients call this number. Also used for SMS stock updates.':
            'Abarwayi bahamagara iyi nimero. Ikoreshwa kandi kuvugurura ububiko ukoresheje SMS.',
        'I confirm this pharmacy is licensed and I accept the terms and privacy notice.':
            'Nemeza ko iyi farumasi ifite uruhushya kandi nemeye amabwiriza n’itangazo ryerekeye amakuru bwite.',
        'New password': 'Ijambo ry’ibanga rishya',
        'New password confirmation': 'Ongera wandike ijambo ry’ibanga rishya',
        'Old password': 'Ijambo ry’ibanga risanzwe',
        'This field is required.': 'Iki gice kigomba kuzuzwa.',
        'Enter a valid email address.': 'Andika imeyili nyayo.',
        'The two password fields didn’t match.': 'Amagambo y’ibanga yombi ntabwo ahuye.',
        'Your old password was entered incorrectly. Please enter it again.':
            'Ijambo ry’ibanga risanzwe ntiryanditswe neza. Ongera urishyiremo.',
        'This password is too short. It must contain at least 8 characters.':
            'Iri jambo ry’ibanga ni rigufi cyane. Rigomba kugira nibura inyuguti 8.',
        'This password is too common.': 'Iri jambo ry’ibanga rirasanzwe cyane.',
        'This password is entirely numeric.': 'Iri jambo ry’ibanga rigizwe n’imibare gusa.',
        'This username is taken.': 'Iri zina ryamaze gufatwa.',
        'This license is already registered.': 'Uru ruhushya rwamaze kwandikwa.',
        'The location must be inside Rwanda.': 'Aho farumasi iri hagomba kuba mu Rwanda.',
        'Enter a valid Rwandan mobile number (MTN 078/079 or Airtel 072/073).':
            'Andika nimero ya telefoni yo mu Rwanda nyayo (MTN 078/079 cyangwa Airtel 072/073).',
        'The license file is too large (maximum 5 MB).': 'Dosiye y’uruhushya ni nini cyane (ntirenze MB 5).',
        'Upload the license as a PDF, JPG or PNG file.': 'Ohereza uruhushya nka dosiye ya PDF, JPG cyangwa PNG.',
        'This file does not look like a PDF or image.': 'Iyi dosiye ntisa na PDF cyangwa ifoto.',
        'The prescription file is too large (maximum 5 MB).': 'Dosiye y’urupapuro rwa muganga ni nini cyane (ntirenze MB 5).',
        'Upload the prescription as a photo (JPG or PNG) or a PDF.':
            'Ohereza urupapuro rwa muganga nk’ifoto (JPG cyangwa PNG) cyangwa PDF.',
        'This file does not look like a photo or PDF.': 'Iyi dosiye ntisa n’ifoto cyangwa PDF.',

        # --- Pharmacy login and password ---
        'Partners': 'Abafatanyabikorwa',
        'Pharmacy login': 'Kwinjira kwa farumasi',
        'Manage your stock and reservations.': 'Genzura ububiko bwawe n’imiti yabikishijwe.',
        'Wrong username or password.': 'Izina cyangwa ijambo ry’ibanga si byo.',
        'Log in': 'Injira',
        'Forgot your password?': 'Wibagiwe ijambo ry’ibanga?',
        'New pharmacy?': 'Uri farumasi nshya?',
        'Register here': 'Iyandikishe hano',
        'Back to search': 'Subira ku ishakisha',
        'Enter your login code': 'Andika kode yo kwinjira',
        'We sent a 6-digit code by SMS to your pharmacy’s phone. It expires in 5 minutes.':
            'Twohereje kode y’imibare 6 kuri SMS kuri telefoni ya farumasi yawe. Irarangira mu minota 5.',
        'Confirm and log in': 'Emeza winjire',
        'No SMS?': 'Nta SMS wabonye?',
        'Log in again': 'Ongera winjire',
        'to get a new code.': 'kugira ngo ubone kode nshya.',
        'Link expired': 'Ihuza ryarangiye',
        'This password reset link is invalid or was already used.':
            'Iri huza ryo guhindura ijambo ry’ibanga si ryo cyangwa ryamaze gukoreshwa.',
        'Request a new one': 'Saba irindi',
        'Choose a new password': 'Hitamo ijambo ry’ibanga rishya',
        'Use at least 8 characters, not only numbers.': 'Koresha nibura inyuguti 8, kandi atari imibare gusa.',
        'Enter the email address you registered with. We will send you a link to choose a new password.':
            'Andika imeyili wiyandikishijeho. Turakoherereza ihuza ryo guhitamo ijambo ry’ibanga rishya.',
        'Change password': 'Hindura ijambo ry’ibanga',
        'For security, enter your current password first.': 'Ku bw’umutekano, banza wandike ijambo ry’ibanga usanzwe ukoresha.',
        'Send reset link': 'Ohereza ihuza',
        'Save password': 'Bika ijambo ry’ibanga',
        '← Back to dashboard': '← Subira ku mbonerahamwe',
        '← Back to login': '← Subira aho winjirira',
        'Check your email': 'Reba imeyili yawe',
        'If an account exists for that address, we have sent a link to reset your password. It can take a few minutes. Check your spam folder too.':
            'Niba hari konti ifite iyo aderesi, twohereje ihuza ryo guhindura ijambo ry’ibanga. Bishobora gutwara iminota mike. Reba no mu bubiko bwa spam.',
        'Back to login': 'Subira aho winjirira',
        'Password changed': 'Ijambo ry’ibanga ryahinduwe',
        'Your new password is saved. You can now log in.': 'Ijambo ry’ibanga rishya ryabitswe. Ubu ushobora kwinjira.',
        'Your new password is saved.': 'Ijambo ry’ibanga rishya ryabitswe.',
        'Back to dashboard': 'Subira ku mbonerahamwe',
        'Too many attempts': 'Wagerageje inshuro nyinshi',
        'For security, please wait about {n} minutes and try again.':
            'Ku bw’umutekano, tegereza nk’iminota {n} hanyuma wongere ugerageze.',

        # --- Register a pharmacy ---
        'Register your pharmacy': 'Andikisha farumasi yawe',
        'Free to start': 'Gutangira ni ubuntu',
        'Your pharmacy stays hidden from patients until an administrator verifies your Rwanda FDA license.':
            'Farumasi yawe ntigaragara ku barwayi kugeza umuyobozi agenzuye uruhushya rwawe rwa Rwanda FDA.',
        'Account': 'Konti',
        'You will use these to log in to your dashboard.': 'Uzabikoresha winjira mu mbonerahamwe yawe.',
        'Pharmacy details': 'Amakuru ya farumasi',
        'Location on the map': 'Aho iri ku ikarita',
        'Click the map to place your pin, or stand in the pharmacy and use your current location.':
            'Kanda ku ikarita ushyireho ikimenyetso, cyangwa uri muri farumasi ukoreshe aho uri ubu.',
        'Use my current location': 'Koresha aho ndi ubu',
        'Location set. Drag the pin to adjust.': 'Aho iri habonetse. Kurura ikimenyetso ukosore.',
        'Location is not available in this browser.': 'Iyi mushakisha ntishobora kubona aho uri.',
        'Getting location…': 'Turimo gushaka aho uri…',
        'Could not get location. Click the map instead.': 'Ntitwabashije kubona aho uri. Kanda ku ikarita.',
        'Read the': 'Soma',
        'Terms of Service': 'Amabwiriza yo gukoresha serivisi',
        '(free {days}-day trial, then {price} RWF/month) and the': '(igerageza ry’ubuntu ry’iminsi {days}, hanyuma {price} RWF ku kwezi) n’',
        'privacy notice': 'itangazo ryerekeye amakuru bwite',
        'Privacy notice': 'Itangazo ryerekeye amakuru bwite',
        'Submit registration': 'Ohereza kwiyandikisha',
        'Already registered? Log in': 'Wariyandikishije? Injira',
        'Your registration was not sent yet. Please correct these problems:': 'Kwiyandikisha kwawe ntikuroherezwa. Banza ukosore ibi bikurikira:',
        'For your security, your browser cleared the licence file: please choose it again before sending.': 'Ku bw’umutekano, mushakisha yawe yakuyeho dosiye y’uruhushya: ongera uyihitemo mbere yo kohereza.',
        'Choose your pharmacy’s location on the map (click the map or use your current location).': 'Hitamo aho farumasi yawe iri ku ikarita (kanda ku ikarita cyangwa ukoreshe aho uri ubu).',
        'The passwords do not match.': 'Amagambo y’ibanga ntabwo ahuye.',
        'Registration received. Log in to add your stock. Your pharmacy appears in search once an administrator verifies your license.': 'Kwiyandikisha kwakiriwe. Injira wongeremo imiti ufite. Farumasi yawe izagaragara mu ishakisha nyuma y’uko umuyobozi agenzuye uruhushya rwawe.',

        # --- Contact ---
        'Contact': 'Twandikire',
        'Talk to us': 'Vugana natwe',
        'Questions about a reservation, joining as a pharmacy, or a partnership. We usually reply the same day.':
            'Ibibazo ku muti wabikishije, kwinjira nka farumasi, cyangwa ubufatanye. Akenshi dusubiza uwo munsi.',
        'Monday to Saturday, 8:00–18:00': 'Kuwa mbere kugeza kuwa gatandatu, 8:00–18:00',
        'Message us any time': 'Twandikire igihe icyo ari cyo cyose',
        'For partnerships and data requests': 'Ku bufatanye no gusaba amakuru',
        'Follow us': 'Dukurikire',
        'Quick answers': 'Ibisubizo byihuse',
        'Lost your pickup code?': 'Wabuze kode yawe?',
        'Track your reservation': 'Kurikirana umuti wabikishije',
        'with the number and code from the SMS, or call the pharmacy.': 'ukoresheje nimero na kode biri muri SMS, cyangwa uhamagare farumasi.',
        'Want to delete your data? Email us from the context of your reservation; see the':
            'Ushaka ko amakuru yawe asibwa? Twandikire kuri imeyili uvuga umuti wabikishije; reba',
        'A pharmacy?': 'Uri farumasi?',
        'See benefits and pricing': 'Reba ibyiza n’ibiciro',
        'Medical question? Ask a pharmacist or doctor: MedFinder does not give medical advice.':
            'Ufite ikibazo cy’ubuzima? Baza umufarumasiye cyangwa muganga: MedFinder ntitanga inama z’ubuvuzi.',
        'Hello MedFinder Rwanda': 'Muraho MedFinder Rwanda',

        # --- Error, offline and status pages ---
        'Error {status}': 'Ikosa {status}',
        'Search for a medicine': 'Shakisha umuti',
        'Go back': 'Subira inyuma',
        'Bad request': 'Icyifuzo kitumvikana',
        'The request could not be understood. Please go back and try again.':
            'Icyo wasabye nticyumvikanye. Subira inyuma wongere ugerageze.',
        'Access denied': 'Ntiwemerewe kwinjira',
        'You do not have permission to open this page.': 'Ntiwemerewe gufungura iyi paji.',
        'Page not found': 'Paji ntibonetse',
        'The page you are looking for does not exist or has moved. Check the link or search for a medicine.':
            'Paji ushaka ntibaho cyangwa yimuwe. Reba neza ihuza cyangwa ushakishe umuti.',
        'Something went wrong': 'Habaye ikibazo',
        'An unexpected error happened on our side. Please try again in a moment.':
            'Habaye ikosa ritunguranye kuri twe. Ongera ugerageze mu kanya.',
        'Your session expired': 'Igihe cyawe cyarangiye',
        'For your security, the form expired. Go back, refresh the page and submit it again.':
            'Ku bw’umutekano wawe, ifishi yarangiye. Subira inyuma, ongera ufungure paji hanyuma wohereze bundi bushya.',
        'Offline': 'Nta interineti',
        'You are offline': 'Nta interineti ufite',
        'MedFinder needs a connection to show live stock. Check your data or Wi-Fi and try again.':
            'MedFinder ikenera interineti kugira ngo yerekane imiti iri mu bubiko ubu. Reba data cyangwa Wi-Fi yawe wongere ugerageze.',
        'No internet at all? Dial': 'Nta interineti na mba? Kanda',
        'from any phone to search and reserve by USSD.': 'kuri telefoni iyo ari yo yose ushakishe kandi ubikishe ukoresheje USSD.',
        'Try again': 'Ongera ugerageze',
        'Service status': 'Uko serivisi zikora',
        'All systems working': 'Serivisi zose zirakora',
        'Some services have problems': 'Serivisi zimwe zifite ibibazo',
        'Service disruption': 'Serivisi zahagaze',
        'Checked {when} (Kigali time).': 'Byagenzuwe {when} (isaha ya Kigali).',
        'Website and database': 'Urubuga n’ububiko bw’amakuru',
        'Working': 'Birakora',
        'Database not reachable': 'Ububiko bw’amakuru ntibugerwaho',
        'Test mode (messages are logged, not sent)': 'Igerageza (ubutumwa burabikwa, ntiburohereza)',
        'The last message failed to send': 'Ubutumwa bwa nyuma ntibwoherejwe',
        'Test mode': 'Igerageza',
        'Test mode (simulation)': 'Igerageza (ntibikorwa by’ukuri)',
        'Monitoring services can check': 'Serivisi zigenzura zishobora kureba',
        'which returns': 'isubiza',
    },

    'fr': {
        # --- Shared page parts and hidden labels for screen readers ---
        'Open menu': 'Ouvrir le menu',
        'Main navigation': 'Navigation principale',
        'Popular medicine searches': 'Recherches fréquentes',
        'Filters': 'Filtres',
        'Map of pharmacies with this medicine': 'Carte des pharmacies qui ont ce médicament',
        'MedFinder in numbers': 'MedFinder en chiffres',
        'Service highlights': 'Points forts du service',
        'Pharmacy partner access': 'Accès des pharmacies partenaires',
        'Breadcrumb': 'Fil d’Ariane',
        'Pages': 'Pages',
        'Pickup code': 'Code de retrait',
        'District': 'District',
        '(optional)': '(facultatif)',
        'Pharmacy': 'Pharmacie',
        'Address': 'Adresse',
        'Reserved': 'Réservé le',
        'Rating': 'Note',
        'Reserve': 'Réserver',

        # --- Form fields (labels, hints, errors) ---
        'Username': 'Nom d’utilisateur',
        'Email': 'E-mail',
        'Password': 'Mot de passe',
        'Confirm password': 'Confirmer le mot de passe',
        'Pharmacy name': 'Nom de la pharmacie',
        'License number': 'Numéro de licence',
        'License document': 'Document de licence',
        'Phone': 'Téléphone',
        'Latitude': 'Latitude',
        'Longitude': 'Longitude',
        'Choose district…': 'Choisissez le district…',
        'Used to log in.': 'Sert à vous connecter.',
        'Used to reset your password if you forget it.': 'Sert à réinitialiser votre mot de passe si vous l’oubliez.',
        'At least 8 characters, not only numbers.': 'Au moins 8 caractères, pas uniquement des chiffres.',
        'As issued by Rwanda FDA.': 'Tel que délivré par Rwanda FDA.',
        'Photo or scan of your Rwanda FDA license: PDF, JPG or PNG, max 5 MB.':
            'Photo ou scan de votre licence Rwanda FDA : PDF, JPG ou PNG, 5 Mo maximum.',
        'Street, landmark or building (optional).': 'Rue, point de repère ou bâtiment (facultatif).',
        'Patients call this number. Also used for SMS stock updates.':
            'Les patients appellent ce numéro. Il sert aussi aux mises à jour du stock par SMS.',
        'I confirm this pharmacy is licensed and I accept the terms and privacy notice.':
            'Je confirme que cette pharmacie dispose d’une licence et j’accepte les conditions et la politique de confidentialité.',
        'This username is taken.': 'Ce nom d’utilisateur est déjà pris.',
        'This license is already registered.': 'Cette licence est déjà enregistrée.',
        'The location must be inside Rwanda.': 'L’emplacement doit se trouver au Rwanda.',
        'Enter a valid Rwandan mobile number (MTN 078/079 or Airtel 072/073).':
            'Saisissez un numéro mobile rwandais valide (MTN 078/079 ou Airtel 072/073).',
        'The license file is too large (maximum 5 MB).': 'Le fichier de licence est trop volumineux (5 Mo maximum).',
        'Upload the license as a PDF, JPG or PNG file.': 'Envoyez la licence en PDF, JPG ou PNG.',
        'This file does not look like a PDF or image.': 'Ce fichier ne semble pas être un PDF ou une image.',
        'The prescription file is too large (maximum 5 MB).': 'Le fichier de l’ordonnance est trop volumineux (5 Mo maximum).',
        'Upload the prescription as a photo (JPG or PNG) or a PDF.': 'Envoyez l’ordonnance en photo (JPG ou PNG) ou en PDF.',
        'This file does not look like a photo or PDF.': 'Ce fichier ne semble pas être une photo ou un PDF.',

        # --- Pharmacy login and password ---
        'Partners': 'Partenaires',
        'Pharmacy login': 'Espace pharmacie',
        'Manage your stock and reservations.': 'Gérez votre stock et vos réservations.',
        'Wrong username or password.': 'Nom d’utilisateur ou mot de passe incorrect.',
        'Log in': 'Se connecter',
        'Forgot your password?': 'Mot de passe oublié ?',
        'New pharmacy?': 'Nouvelle pharmacie ?',
        'Register here': 'Inscrivez-vous ici',
        'Back to search': 'Retour à la recherche',
        'Enter your login code': 'Saisissez votre code de connexion',
        'We sent a 6-digit code by SMS to your pharmacy’s phone. It expires in 5 minutes.':
            'Nous avons envoyé un code à 6 chiffres par SMS au téléphone de votre pharmacie. Il expire dans 5 minutes.',
        'Confirm and log in': 'Confirmer et se connecter',
        'No SMS?': 'Pas de SMS ?',
        'Log in again': 'Reconnectez-vous',
        'to get a new code.': 'pour recevoir un nouveau code.',
        'Link expired': 'Lien expiré',
        'This password reset link is invalid or was already used.':
            'Ce lien de réinitialisation est invalide ou a déjà été utilisé.',
        'Request a new one': 'Demandez-en un nouveau',
        'Choose a new password': 'Choisissez un nouveau mot de passe',
        'Use at least 8 characters, not only numbers.': 'Utilisez au moins 8 caractères, pas uniquement des chiffres.',
        'Enter the email address you registered with. We will send you a link to choose a new password.':
            'Saisissez l’adresse e-mail utilisée lors de l’inscription. Nous vous enverrons un lien pour choisir un nouveau mot de passe.',
        'Change password': 'Changer le mot de passe',
        'For security, enter your current password first.': 'Par sécurité, saisissez d’abord votre mot de passe actuel.',
        'Send reset link': 'Envoyer le lien',
        'Save password': 'Enregistrer le mot de passe',
        '← Back to dashboard': '← Retour au tableau de bord',
        '← Back to login': '← Retour à la connexion',
        'Check your email': 'Consultez vos e-mails',
        'If an account exists for that address, we have sent a link to reset your password. It can take a few minutes. Check your spam folder too.':
            'Si un compte existe pour cette adresse, nous avons envoyé un lien pour réinitialiser votre mot de passe. Cela peut prendre quelques minutes. Vérifiez aussi vos courriers indésirables.',
        'Back to login': 'Retour à la connexion',
        'Password changed': 'Mot de passe modifié',
        'Your new password is saved. You can now log in.': 'Votre nouveau mot de passe est enregistré. Vous pouvez vous connecter.',
        'Your new password is saved.': 'Votre nouveau mot de passe est enregistré.',
        'Back to dashboard': 'Retour au tableau de bord',
        'Too many attempts': 'Trop de tentatives',
        'For security, please wait about {n} minutes and try again.':
            'Par sécurité, patientez environ {n} minutes puis réessayez.',

        # --- Register a pharmacy ---
        'Register your pharmacy': 'Inscrire votre pharmacie',
        'Free to start': 'Gratuit pour commencer',
        'Your pharmacy stays hidden from patients until an administrator verifies your Rwanda FDA license.':
            'Votre pharmacie reste invisible pour les patients jusqu’à ce qu’un administrateur vérifie votre licence Rwanda FDA.',
        'Account': 'Compte',
        'You will use these to log in to your dashboard.': 'Ils vous serviront à vous connecter à votre tableau de bord.',
        'Pharmacy details': 'Informations sur la pharmacie',
        'Location on the map': 'Emplacement sur la carte',
        'Click the map to place your pin, or stand in the pharmacy and use your current location.':
            'Cliquez sur la carte pour placer le repère, ou utilisez votre position actuelle depuis la pharmacie.',
        'Use my current location': 'Utiliser ma position actuelle',
        'Location set. Drag the pin to adjust.': 'Position enregistrée. Déplacez le repère pour ajuster.',
        'Location is not available in this browser.': 'La localisation n’est pas disponible dans ce navigateur.',
        'Getting location…': 'Recherche de la position…',
        'Could not get location. Click the map instead.': 'Position introuvable. Cliquez plutôt sur la carte.',
        'Read the': 'Lisez les',
        'Terms of Service': 'Conditions d’utilisation',
        '(free {days}-day trial, then {price} RWF/month) and the': '(essai gratuit de {days} jours, puis {price} RWF/mois) et la',
        'privacy notice': 'politique de confidentialité',
        'Privacy notice': 'Politique de confidentialité',
        'Submit registration': 'Envoyer l’inscription',
        'Already registered? Log in': 'Déjà inscrit ? Connectez-vous',
        'Your registration was not sent yet. Please correct these problems:': 'Votre inscription n’a pas encore été envoyée. Corrigez ces problèmes :',
        'For your security, your browser cleared the licence file: please choose it again before sending.': 'Pour votre sécurité, votre navigateur a retiré le fichier de licence : choisissez-le à nouveau avant d’envoyer.',
        'Choose your pharmacy’s location on the map (click the map or use your current location).': 'Indiquez l’emplacement de votre pharmacie sur la carte (cliquez sur la carte ou utilisez votre position actuelle).',
        'The passwords do not match.': 'Les mots de passe ne correspondent pas.',
        'Registration received. Log in to add your stock. Your pharmacy appears in search once an administrator verifies your license.': 'Inscription reçue. Connectez-vous pour ajouter votre stock. Votre pharmacie apparaîtra dans la recherche dès qu’un administrateur aura vérifié votre licence.',

        # --- Contact ---
        'Contact': 'Contact',
        'Talk to us': 'Parlez-nous',
        'Questions about a reservation, joining as a pharmacy, or a partnership. We usually reply the same day.':
            'Une question sur une réservation, l’inscription d’une pharmacie ou un partenariat ? Nous répondons généralement le jour même.',
        'Monday to Saturday, 8:00–18:00': 'Du lundi au samedi, 8 h–18 h',
        'Message us any time': 'Écrivez-nous à tout moment',
        'For partnerships and data requests': 'Pour les partenariats et les demandes de données',
        'Follow us': 'Suivez-nous',
        'Quick answers': 'Réponses rapides',
        'Lost your pickup code?': 'Code de retrait perdu ?',
        'Track your reservation': 'Suivez votre réservation',
        'with the number and code from the SMS, or call the pharmacy.': 'avec le numéro et le code du SMS, ou appelez la pharmacie.',
        'Want to delete your data? Email us from the context of your reservation; see the':
            'Vous voulez supprimer vos données ? Écrivez-nous en précisant votre réservation ; voir la',
        'A pharmacy?': 'Vous êtes une pharmacie ?',
        'See benefits and pricing': 'Voir les avantages et les tarifs',
        'Medical question? Ask a pharmacist or doctor: MedFinder does not give medical advice.':
            'Une question médicale ? Demandez à un pharmacien ou à un médecin : MedFinder ne donne pas de conseils médicaux.',
        'Hello MedFinder Rwanda': 'Bonjour MedFinder Rwanda',

        # --- Error, offline and status pages ---
        'Error {status}': 'Erreur {status}',
        'Search for a medicine': 'Chercher un médicament',
        'Go back': 'Retour',
        'Bad request': 'Requête incorrecte',
        'The request could not be understood. Please go back and try again.':
            'La demande n’a pas pu être comprise. Revenez en arrière et réessayez.',
        'Access denied': 'Accès refusé',
        'You do not have permission to open this page.': 'Vous n’avez pas l’autorisation d’ouvrir cette page.',
        'Page not found': 'Page introuvable',
        'The page you are looking for does not exist or has moved. Check the link or search for a medicine.':
            'La page demandée n’existe pas ou a été déplacée. Vérifiez le lien ou cherchez un médicament.',
        'Something went wrong': 'Un problème est survenu',
        'An unexpected error happened on our side. Please try again in a moment.':
            'Une erreur inattendue s’est produite de notre côté. Réessayez dans un instant.',
        'Your session expired': 'Votre session a expiré',
        'For your security, the form expired. Go back, refresh the page and submit it again.':
            'Pour votre sécurité, le formulaire a expiré. Revenez en arrière, actualisez la page et renvoyez-le.',
        'Offline': 'Hors ligne',
        'You are offline': 'Vous êtes hors ligne',
        'MedFinder needs a connection to show live stock. Check your data or Wi-Fi and try again.':
            'MedFinder a besoin d’une connexion pour afficher le stock en direct. Vérifiez vos données mobiles ou le Wi-Fi et réessayez.',
        'No internet at all? Dial': 'Pas d’internet du tout ? Composez le',
        'from any phone to search and reserve by USSD.': 'depuis n’importe quel téléphone pour chercher et réserver par USSD.',
        'Try again': 'Réessayer',
        'Service status': 'État du service',
        'All systems working': 'Tous les services fonctionnent',
        'Some services have problems': 'Certains services rencontrent des problèmes',
        'Service disruption': 'Service perturbé',
        'Checked {when} (Kigali time).': 'Vérifié le {when} (heure de Kigali).',
        'Website and database': 'Site web et base de données',
        'Working': 'Fonctionne',
        'Database not reachable': 'Base de données injoignable',
        'Test mode (messages are logged, not sent)': 'Mode test (messages enregistrés, non envoyés)',
        'The last message failed to send': 'Le dernier message n’a pas pu être envoyé',
        'Test mode': 'Mode test',
        'Test mode (simulation)': 'Mode test (simulation)',
        'Monitoring services can check': 'Les services de surveillance peuvent consulter',
        'which returns': 'qui renvoie',
    },
}


def translate(text, lang):
    """The text in `lang`, or the English text itself when there is no translation."""
    text = str(text)
    if lang == 'en':
        return text
    table = TEXT.get(lang, {})
    if text in table:
        return table[text]
    # Django's own messages use a straight apostrophe in some versions and a curly one in others.
    return table.get(text.replace("'", '’'), text)
