import streamlit_authenticator as stauth

# Vul hier je gewenste wachtwoord in
wachtwoord = ["Test123"] 
# Maak de hash
hashed = stauth.Hasher(wachtwoord).generate()
print(hashed)
