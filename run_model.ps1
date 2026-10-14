param([switch]$Reset)
if ($Reset) { python .\run_model.py --reset } else { python .\run_model.py }
