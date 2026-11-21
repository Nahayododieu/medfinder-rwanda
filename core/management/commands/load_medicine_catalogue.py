"""Add a catalogue of common medicines used in Rwanda (no prices).

    python manage.py load_medicine_catalogue

The list follows medicines on Rwanda's essential medicines list and those commonly
stocked by community pharmacies. It contains NO prices: each pharmacy enters its own
real price when it adds stock. Official reference prices (e.g. the RSSB tariff) can be
added per medicine in the admin, together with their source and date.

Safe to run more than once: existing medicines (same name) are left unchanged.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Medicine

# name, generic name, category, prescription required
CATALOGUE = [
    # Pain relief and fever
    ('Paracetamol 500mg', 'Paracetamol', 'Pain relief', False),
    ('Paracetamol syrup 120mg/5ml', 'Paracetamol', 'Pain relief', False),
    ('Ibuprofen 200mg', 'Ibuprofen', 'Pain relief', False),
    ('Ibuprofen 400mg', 'Ibuprofen', 'Pain relief', False),
    ('Ibuprofen syrup 100mg/5ml', 'Ibuprofen', 'Pain relief', False),
    ('Diclofenac 50mg', 'Diclofenac', 'Pain relief', False),
    ('Diclofenac gel 1%', 'Diclofenac', 'Pain relief', False),
    ('Aspirin 300mg', 'Acetylsalicylic acid', 'Pain relief', False),
    ('Tramadol 50mg', 'Tramadol', 'Pain relief', True),
    ('Mefenamic acid 500mg', 'Mefenamic acid', 'Pain relief', False),

    # Antibiotics
    ('Amoxicillin 250mg', 'Amoxicillin', 'Antibiotic', True),
    ('Amoxicillin 500mg', 'Amoxicillin', 'Antibiotic', True),
    ('Amoxicillin syrup 125mg/5ml', 'Amoxicillin', 'Antibiotic', True),
    ('Augmentin 625mg', 'Amoxicillin + Clavulanic acid', 'Antibiotic', True),
    ('Amoxicillin + Clavulanic acid 1g', 'Amoxicillin + Clavulanic acid', 'Antibiotic', True),
    ('Azithromycin 500mg', 'Azithromycin', 'Antibiotic', True),
    ('Azithromycin syrup 200mg/5ml', 'Azithromycin', 'Antibiotic', True),
    ('Ciprofloxacin 500mg', 'Ciprofloxacin', 'Antibiotic', True),
    ('Cotrimoxazole 480mg', 'Sulfamethoxazole + Trimethoprim', 'Antibiotic', True),
    ('Cotrimoxazole syrup 240mg/5ml', 'Sulfamethoxazole + Trimethoprim', 'Antibiotic', True),
    ('Doxycycline 100mg', 'Doxycycline', 'Antibiotic', True),
    ('Erythromycin 250mg', 'Erythromycin', 'Antibiotic', True),
    ('Cloxacillin 500mg', 'Cloxacillin', 'Antibiotic', True),
    ('Cefixime 200mg', 'Cefixime', 'Antibiotic', True),
    ('Cefuroxime 500mg', 'Cefuroxime', 'Antibiotic', True),
    ('Ceftriaxone injection 1g', 'Ceftriaxone', 'Antibiotic', True),
    ('Gentamicin injection 80mg/2ml', 'Gentamicin', 'Antibiotic', True),
    ('Metronidazole 250mg', 'Metronidazole', 'Antibiotic', True),
    ('Metronidazole 500mg', 'Metronidazole', 'Antibiotic', True),
    ('Nitrofurantoin 100mg', 'Nitrofurantoin', 'Antibiotic', True),
    ('Phenoxymethylpenicillin 250mg', 'Penicillin V', 'Antibiotic', True),
    ('Tinidazole 500mg', 'Tinidazole', 'Antibiotic', True),

    # Malaria
    ('Coartem 20/120', 'Artemether + Lumefantrine', 'Antimalarial', True),
    ('Artesunate injection 60mg', 'Artesunate', 'Antimalarial', True),
    ('Quinine 300mg', 'Quinine sulfate', 'Antimalarial', True),
    ('Dihydroartemisinin + Piperaquine 40/320mg', 'Dihydroartemisinin + Piperaquine', 'Antimalarial', True),

    # Worms and parasites
    ('Albendazole 400mg', 'Albendazole', 'Antiparasitic', False),
    ('Mebendazole 100mg', 'Mebendazole', 'Antiparasitic', False),
    ('Mebendazole 500mg', 'Mebendazole', 'Antiparasitic', False),
    ('Praziquantel 600mg', 'Praziquantel', 'Antiparasitic', True),
    ('Ivermectin 3mg', 'Ivermectin', 'Antiparasitic', True),
    ('Permethrin cream 5%', 'Permethrin', 'Antiparasitic', False),
    ('Benzyl benzoate lotion 25%', 'Benzyl benzoate', 'Antiparasitic', False),

    # Fungal infections
    ('Fluconazole 150mg', 'Fluconazole', 'Antifungal', True),
    ('Clotrimazole cream 1%', 'Clotrimazole', 'Antifungal', False),
    ('Clotrimazole vaginal tablet 500mg', 'Clotrimazole', 'Antifungal', False),
    ('Miconazole cream 2%', 'Miconazole', 'Antifungal', False),
    ('Nystatin oral suspension', 'Nystatin', 'Antifungal', False),
    ('Griseofulvin 500mg', 'Griseofulvin', 'Antifungal', True),
    ('Terbinafine cream 1%', 'Terbinafine', 'Antifungal', False),

    # Allergy, cough and cold
    ('Cetirizine 10mg', 'Cetirizine', 'Allergy', False),
    ('Loratadine 10mg', 'Loratadine', 'Allergy', False),
    ('Chlorphenamine 4mg', 'Chlorphenamine', 'Allergy', False),
    ('Promethazine 25mg', 'Promethazine', 'Allergy', False),
    ('Desloratadine 5mg', 'Desloratadine', 'Allergy', False),
    ('Cough syrup (dextromethorphan)', 'Dextromethorphan', 'Cough & cold', False),
    ('Bromhexine 8mg', 'Bromhexine', 'Cough & cold', False),
    ('Carbocisteine syrup', 'Carbocisteine', 'Cough & cold', False),
    ('Saline nasal drops 0.9%', 'Sodium chloride', 'Cough & cold', False),
    ('Xylometazoline nasal drops', 'Xylometazoline', 'Cough & cold', False),

    # Breathing
    ('Salbutamol inhaler', 'Salbutamol', 'Respiratory', True),
    ('Salbutamol 4mg', 'Salbutamol', 'Respiratory', True),
    ('Beclomethasone inhaler', 'Beclometasone', 'Respiratory', True),
    ('Aminophylline 100mg', 'Aminophylline', 'Respiratory', True),
    ('Montelukast 10mg', 'Montelukast', 'Respiratory', True),

    # Stomach and digestion
    ('Omeprazole 20mg', 'Omeprazole', 'Digestive', False),
    ('Esomeprazole 40mg', 'Esomeprazole', 'Digestive', True),
    ('Pantoprazole 40mg', 'Pantoprazole', 'Digestive', True),
    ('Antacid tablets (aluminium/magnesium hydroxide)', 'Aluminium hydroxide + Magnesium hydroxide', 'Digestive', False),
    ('Magnesium trisilicate', 'Magnesium trisilicate', 'Digestive', False),
    ('Hyoscine butylbromide 10mg', 'Hyoscine butylbromide', 'Digestive', False),
    ('Metoclopramide 10mg', 'Metoclopramide', 'Digestive', True),
    ('Domperidone 10mg', 'Domperidone', 'Digestive', True),
    ('Loperamide 2mg', 'Loperamide', 'Digestive', False),
    ('Bisacodyl 5mg', 'Bisacodyl', 'Digestive', False),
    ('Lactulose syrup', 'Lactulose', 'Digestive', False),
    ('ORS sachet', 'Oral rehydration salts', 'Rehydration', False),
    ('Zinc 20mg', 'Zinc sulfate', 'Supplement', False),

    # Diabetes
    ('Metformin 500mg', 'Metformin', 'Diabetes', True),
    ('Metformin 850mg', 'Metformin', 'Diabetes', True),
    ('Glibenclamide 5mg', 'Glibenclamide', 'Diabetes', True),
    ('Gliclazide 80mg', 'Gliclazide', 'Diabetes', True),
    ('Insulin (Actrapid)', 'Insulin human', 'Diabetes', True),
    ('Insulin (Mixtard 30/70)', 'Insulin human (premixed)', 'Diabetes', True),

    # Blood pressure and heart
    ('Amlodipine 5mg', 'Amlodipine', 'Blood pressure', True),
    ('Amlodipine 10mg', 'Amlodipine', 'Blood pressure', True),
    ('Nifedipine 20mg', 'Nifedipine', 'Blood pressure', True),
    ('Hydrochlorothiazide 25mg', 'Hydrochlorothiazide', 'Blood pressure', True),
    ('Enalapril 10mg', 'Enalapril', 'Blood pressure', True),
    ('Captopril 25mg', 'Captopril', 'Blood pressure', True),
    ('Lisinopril 10mg', 'Lisinopril', 'Blood pressure', True),
    ('Losartan 50mg', 'Losartan', 'Blood pressure', True),
    ('Atenolol 50mg', 'Atenolol', 'Blood pressure', True),
    ('Bisoprolol 5mg', 'Bisoprolol', 'Heart', True),
    ('Furosemide 40mg', 'Furosemide', 'Heart', True),
    ('Spironolactone 25mg', 'Spironolactone', 'Heart', True),
    ('Aspirin 75mg', 'Acetylsalicylic acid', 'Heart', True),
    ('Atorvastatin 20mg', 'Atorvastatin', 'Heart', True),
    ('Simvastatin 20mg', 'Simvastatin', 'Heart', True),

    # Brain, nerves and mental health
    ('Carbamazepine 200mg', 'Carbamazepine', 'Epilepsy', True),
    ('Phenobarbital 30mg', 'Phenobarbital', 'Epilepsy', True),
    ('Sodium valproate 200mg', 'Sodium valproate', 'Epilepsy', True),
    ('Phenytoin 100mg', 'Phenytoin', 'Epilepsy', True),
    ('Diazepam 5mg', 'Diazepam', 'Mental health', True),
    ('Amitriptyline 25mg', 'Amitriptyline', 'Mental health', True),
    ('Fluoxetine 20mg', 'Fluoxetine', 'Mental health', True),
    ('Haloperidol 5mg', 'Haloperidol', 'Mental health', True),
    ('Risperidone 2mg', 'Risperidone', 'Mental health', True),

    # Steroids and hormones
    ('Prednisolone 5mg', 'Prednisolone', 'Steroid', True),
    ('Dexamethasone 4mg', 'Dexamethasone', 'Steroid', True),
    ('Levothyroxine 50mcg', 'Levothyroxine', 'Hormone', True),

    # Women's health and family planning
    ('Levonorgestrel 1.5mg (emergency pill)', 'Levonorgestrel', 'Family planning', True),
    ('Combined oral contraceptive (Microgynon)', 'Levonorgestrel + Ethinylestradiol', 'Family planning', True),
    ('Medroxyprogesterone injection 150mg (Depo-Provera)', 'Medroxyprogesterone acetate', 'Family planning', True),
    ('Male condoms', 'Condom', 'Family planning', False),
    ('Misoprostol 200mcg', 'Misoprostol', "Women's health", True),
    ('Oxytocin injection 10IU', 'Oxytocin', "Women's health", True),
    ('Tranexamic acid 500mg', 'Tranexamic acid', "Women's health", True),
    ('Ferrous sulfate + Folic acid', 'Iron + Folic acid', "Women's health", False),

    # Vitamins and supplements
    ('Vitamin C 500mg', 'Ascorbic acid', 'Supplement', False),
    ('Folic acid 5mg', 'Folic acid', 'Supplement', False),
    ('Ferrous sulfate 200mg', 'Iron', 'Supplement', False),
    ('Vitamin B complex', 'Vitamin B complex', 'Supplement', False),
    ('Multivitamin tablets', 'Multivitamins', 'Supplement', False),
    ('Multivitamin syrup', 'Multivitamins', 'Supplement', False),
    ('Calcium + Vitamin D3', 'Calcium carbonate + Colecalciferol', 'Supplement', False),
    ('Vitamin A 200,000 IU', 'Retinol', 'Supplement', False),

    # Skin
    ('Hydrocortisone cream 1%', 'Hydrocortisone', 'Skin', False),
    ('Betamethasone cream 0.1%', 'Betamethasone', 'Skin', True),
    ('Calamine lotion', 'Calamine', 'Skin', False),
    ('Silver sulfadiazine cream 1%', 'Silver sulfadiazine', 'Skin', True),
    ('Fusidic acid cream 2%', 'Fusidic acid', 'Skin', True),
    ('Gentian violet 0.5%', 'Gentian violet', 'Skin', False),

    # Eyes and ears
    ('Tetracycline eye ointment 1%', 'Tetracycline', 'Eye & ear', False),
    ('Chloramphenicol eye drops 0.5%', 'Chloramphenicol', 'Eye & ear', True),
    ('Ciprofloxacin eye/ear drops 0.3%', 'Ciprofloxacin', 'Eye & ear', True),
    ('Gentamicin eye drops 0.3%', 'Gentamicin', 'Eye & ear', True),

    # First aid
    ('Povidone-iodine 10% solution', 'Povidone-iodine', 'First aid', False),
    ('Chlorhexidine solution', 'Chlorhexidine', 'First aid', False),
    ('Methylated spirit', 'Ethanol (denatured)', 'First aid', False),
    ('Sterile gauze', 'Gauze', 'First aid', False),
    ('Adhesive bandage (plasters)', 'Plasters', 'First aid', False),
    ('Crepe bandage', 'Bandage', 'First aid', False),

    # Antivirals
    ('Acyclovir 400mg', 'Aciclovir', 'Antiviral', True),
    ('Acyclovir cream 5%', 'Aciclovir', 'Antiviral', False),
]


class Command(BaseCommand):
    help = 'Add a catalogue of common medicines used in Rwanda (names only, no prices).'

    @transaction.atomic
    def handle(self, *args, **opts):
        added = 0
        for name, generic, category, rx in CATALOGUE:
            _, created = Medicine.objects.get_or_create(name=name, defaults={
                'generic_name': generic, 'category': category, 'requires_prescription': rx})
            added += created
        self.stdout.write(self.style.SUCCESS(
            f'{added} medicine(s) added, {len(CATALOGUE) - added} already existed. '
            f'Total in catalogue: {Medicine.objects.count()}.'))
