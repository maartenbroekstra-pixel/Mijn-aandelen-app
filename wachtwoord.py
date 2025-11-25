import streamlit as st
import streamlit_authenticator as stauth

# Vul hieronder je gewenste wachtwoord in
wachtwoord_om_te_testen = ["admin"]

# De hash genereren
hashed = stauth.Hasher(wachtwoord_om_te_testen).hash()

# Het resultaat tonen
st.header("Jouw Wachtwoord Hash")
st.code(hashed[0], language='text')
st.success("Kopieer de code hierboven en plak deze in je App.py bij 'passwords'.")
