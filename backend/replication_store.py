# ── Replikering: modtager-endpoint (algoserveren) ──────────────
# Modtager hele DB-filer (+ orders_log.json / logs.zip) fra workstationerne
# og lægger dem i archives/<source>/ — adskilt fra algoserverens egen DB.
@app.post("/replication/upload")
async def replication_upload(
    request:  Request,
    source:   str = "",
    artifact: str = "db",
    x_internal_key: str = Header(None),
):
    # Maskine-til-maskine-tillid: intern nøgle skal matche
    if not identity.internal_key or x_internal_key != identity.internal_key:
        raise HTTPException(status_code=401, detail="Ugyldig intern nøgle")
    body = await request.body()
    import replication_store
    try:
        result = await asyncio.to_thread(
            replication_store.store_artifact, source, artifact, body
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lagring fejlede: {e}")
    return result


# ── Replikering: hvilke kilder har vi arkiv for? (bruges af Studio i del 3) ──
@app.get("/replication/sources", dependencies=[Depends(require_studio_auth)])
async def replication_sources():
    import replication_store
    return {"sources": replication_store.list_sources()}