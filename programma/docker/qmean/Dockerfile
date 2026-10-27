# Worker QMEAN per MakPhi: immagine ufficiale di QMEAN (SIB/Biozentrum) più un piccolo servizio HTTP interno.
# Il disco con UniClust30/UniRef30 (qualunque versione, anche più versioni) va montato in sola lettura su
# /uniclust_src; la QMTL (facoltativa, per QMEANDisCo) su /qmtl. In /cache (volume) finiscono i file
# ricreati per i database HH-suite 2. Immagine di base fissata per digest (QMEAN 4.3.0, HH-suite 3.3.0).
FROM --platform=linux/amd64 registry.scicore.unibas.ch/schwede/qmean@sha256:640916f8714d79601ce5883f72e725d3bcf1856e930c8cb7ce7d972efe08c7b8

USER root
RUN mkdir -p /qmean/makphi /uniclust_src /cache /tmp/uniclust_views \
    && chown -R qmean:qmean /cache /tmp/uniclust_views
COPY qmean_worker.py uniclust.py run_qmean_wrapper.py /qmean/makphi/
USER qmean
# CentOS 7 non ha C.UTF-8: senza, Python 3.6 usa ASCII e i messaggi in italiano mandano in errore il worker
ENV LANG=en_US.UTF-8 PYTHONIOENCODING=utf-8

EXPOSE 8080
VOLUME ["/cache"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=4 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/status', timeout=4)" || exit 1
CMD ["python3", "/qmean/makphi/qmean_worker.py"]
